"""世界任务工具的实际 schema、授权和工厂；普通发言不取得这些工具。"""
import json
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from langchain_core.tools import StructuredTool

from ..db import SessionLocal
from ..models import WorldCoordinationGrant
from ..agent.tools import guard_tools, REJECTED_OUTPUT_PREFIX, FAILED_OUTPUT_PREFIX
from ..agent.tool_definitions import tool_definition
from ..context.domain import ContextBuildError
from . import service, tasks


class Empty(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Delegate(Empty):
    conversation_id: int = Field(gt=0, description='从 world_read_overview 获得的本任务获准群 ID。')
    goal: str = Field(min_length=1, max_length=20000, description='向该群交接的任务目标，不包含无权向该群共享的资料。')
    request_key: str = Field(min_length=1, max_length=128, description='本任务内稳定的委派身份，重试沿用同键。')
    mode: Literal['design', 'execute'] = 'execute'
    definition_id: str | None = Field(default=None, max_length=64)
    expected_graph_revision: int | None = Field(default=None, ge=0)


class Wait(Empty):
    seconds: int = Field(default=20, ge=1, le=60, description='本次只读等待的秒数；不是整个任务时限。')


class Finish(Empty):
    summary: str = Field(min_length=1, max_length=20000, description='依据真实子任务结果总结；有未完成或失败子任务时服务端拒绝完成。')


class Stop(Empty):
    task_id: str = Field(max_length=64)
    expected_revision: int = Field(ge=0)
    child_id: str | None = Field(default=None, max_length=64, description='只停止一个子任务时提供其 ID，此时 expected_revision 使用子任务版本。')


class Replan(Empty):
    child_id: str = Field(max_length=64)
    expected_graph_revision: int = Field(ge=0)
    goal: str = Field(min_length=1, max_length=20000)
    request_key: str = Field(min_length=1, max_length=128)


class ReadTask(Empty):
    task_id: str | None = Field(default=None, max_length=64, description='省略时读取当前任务；也可读取本世界 Owner 的历史任务。')


class SearchMemory(Empty):
    query: str = Field(default='', max_length=200)


class ReadMemory(Empty):
    memory_id: str = Field(max_length=64)
    revision: int = Field(ge=1)


from .memory import Save as SaveMemory


DEFINITIONS = {
    'world_read_overview': (Empty, '读取本次世界任务获准的群、协调者、模板引用与剩余额度。只返回当前 World 的有界概览，不授予额外权限。'),
    'world_read_task': (ReadTask, '读取世界任务的准确状态、群协调/运行引用、错误和根预算。不要凭自己的回复声称子任务已经完成。'),
    'world_delegate_group': (Delegate, '把目标交给已任命协调者的群，复用该群真实图管理与执行。先读取概览；沿用根任务额度，返回持久子任务引用。设计模式只规划，执行模式可启动；不能代签人工确认。'),
    'world_replan_group': (Replan, '针对当前任务的准确群子任务请求局部重规划。使用 read_task 的 child_id/graph_revision，沿用该群原运行、根预算和已执行事实，不代签人工确认。'),
    'world_wait_task': (Wait, '等待已派发子任务到达终态或人工等待，期间不重复调用模型。返回后检查真实状态；超时只结束本次等待，任务继续。'),
    'world_finish_task': (Finish, '在全部子任务确实成功后完成当前世界目标并保存总结；失败、未执行和结果未知不能被文字改写成成功。'),
    'world_stop_task': (Stop, '按准确身份和版本停止当前任务及它派生的群运行，已完成事实保留，不回滚文件。'),
    'world_memory_search': (SearchMemory, '主动搜索本世界岗位约定、决定、偏好与推断；返回来源及版本。模型整理不自动等同事实，停用/失效资料不采用。'),
    'world_memory_read': (ReadMemory, '按搜索得到的记忆 ID 和准确 revision 回读世界约定；来源或版本变化时重新检索。普通角色没有此读取权限。'),
    'world_memory_save': (SaveMemory, '保存本次世界任务相关的长期约定，沿用幂等键；来源固定为当前任务的用户要求，标注为模型整理，不能冒充平台指令。'),
}
READ_ONLY = ['world_read_overview', 'world_read_task', 'world_memory_search', 'world_memory_read']


async def names(session, execution_id):
    if not execution_id:
        return []
    grant = await session.get(WorldCoordinationGrant, execution_id)
    if not grant:
        return []
    await service.authorized(session, execution_id)
    if not grant.task_id:
        return READ_ONLY
    from ..world_types.activities import definitions
    return [*DEFINITIONS, *definitions()]


def specs(selected):
    from ..world_types.activities import specs as activity_specs
    return [tool_definition(name, DEFINITIONS[name][1], DEFINITIONS[name][0]) for name in selected if name in DEFINITIONS] + [s for s in activity_specs() if s['name'] in selected]


async def invoke(execution_id, name, kwargs):
    try:
        if name not in DEFINITIONS:
            from ..world_types.activities import start
            return json.dumps(await start(execution_id, name, kwargs), ensure_ascii=False, default=str)
        body = DEFINITIONS[name][0].model_validate(kwargs)
        if name == 'world_read_overview': result = await tasks.overview(execution_id)
        elif name == 'world_read_task':
            async with SessionLocal() as session:
                grant = await service.authorized(session, execution_id)
                identity = body.task_id or grant.task_id
                result = await tasks.view(session, await tasks.owned(session, identity, grant.owner_id)) if identity else {'task': None}
                await tasks.remember_read(session, execution_id, result)
                await session.commit()
        elif name == 'world_delegate_group': result = await tasks.delegate(execution_id, body)
        elif name == 'world_replan_group': result = await tasks.replan(execution_id, body)
        elif name == 'world_wait_task':
            result = await tasks.wait(execution_id, body.seconds)
            async with SessionLocal() as session:
                await service.authorized(session, execution_id)
                await tasks.remember_read(session, execution_id, result)
                await session.commit()
        elif name == 'world_finish_task': result = await tasks.complete(execution_id, body.summary)
        elif name.startswith('world_memory_'):
            from . import memory
            async with SessionLocal() as session:
                grant = await service.authorized(session, execution_id)
            if name == 'world_memory_search': result = await memory.search(grant.owner_id, body.query, execution_id=execution_id)
            elif name == 'world_memory_read': result = await memory.read(grant.owner_id, body.memory_id, body.revision, execution_id=execution_id)
            else: result = await memory.save(grant.owner_id, body, execution_id=execution_id)
        else:
            async with SessionLocal() as session:
                grant, task = await tasks.authorized(session, execution_id)
                if body.task_id != task.id:
                    raise HTTPException(403, 'WORLD_TASK_TARGET_FORBIDDEN')
            result = await tasks.stop_child(body.task_id, body.child_id, grant.owner_id, body.expected_revision) if body.child_id else await tasks.stop(body.task_id, grant.owner_id, body.expected_revision)
        return json.dumps(result, ensure_ascii=False, default=str)
    except HTTPException as exc:
        return REJECTED_OUTPUT_PREFIX + ' ' + json.dumps({'error': exc.detail}, ensure_ascii=False)
    except ContextBuildError:
        return REJECTED_OUTPUT_PREFIX + ' WORLD_ORCHESTRATOR_REVOKED'
    except Exception:
        return FAILED_OUTPUT_PREFIX + ' WORLD_OPERATION_RESULT_UNKNOWN'


async def create(session, execution_id):
    tools = []
    for name in await names(session, execution_id):
        def implementation(key):
            async def call(**kwargs):
                return await invoke(execution_id, key, kwargs)
            return call
        if name in DEFINITIONS:
            schema, description = DEFINITIONS[name]
        else:
            from ..world_types.activities import schema as activity_schema, definitions
            schema, description = activity_schema(name), definitions()[name].description + ' 由宿主准备并派发，使用原世界任务的额度和取消范围。'
        tools.append(StructuredTool.from_function(coroutine=implementation(name), name=name, description=description, args_schema=schema))
    return guard_tools(tools, allow_dangerous=True)
