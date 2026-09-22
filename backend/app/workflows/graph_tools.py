"""独立图管理工具。公开参数只含设计内容，不接受 Owner/群/目标身份覆盖。"""
import json
from fastapi import HTTPException
from pydantic import ValidationError
from langchain_core.tools import StructuredTool
from ..db import SessionLocal
from ..models import ExecutionAllocation
from ..agent.tools import guard_tools, REJECTED_OUTPUT_PREFIX
from . import graph_schemas as schemas
from .feedback_schemas import FeedbackToolUpdate, FeedbackUpdate

DESCRIPTIONS={
    'workflow_read_graph':'读取本次授权目标的完整图、图版本、合法成员及工具说明、编译问题、保护约束和运行可编辑范围。省略 node_ids 得到完整图；部分读图不能作为整图覆盖依据。graph_revision 可读取历史，历史本身不可改写。',
    'workflow_write_graph':'整体写入完整 graph：在授权新草稿槽位创建，或替换当前目标图。先 read_graph 获取完整图与版本，保留未改节点 ID；遗漏已有节点表示请求删除，不能绕过保护或活跃任务冻结。新节点可省略坐标，由画布按拓扑布局；presentation 可设置阶段和分支文案，省略保留现有展示信息，null 清除。expected_graph_revision 为读到的图版本，新图为 0。mutation_key 标识一次修改；同内容重发保持原键，修改内容换新键。validate_only=true 只校验。全部校验成功才原子提交，保存不启动，错误按字段定位修正。',
    'workflow_edit_graph':'按稳定节点 ID 局部编辑授权图；operations 是带 op 的原子批次。add_node/update_node/remove_node、connect/disconnect、set_inputs/set_condition、upsert_loop/remove_loop、set_entries/set_concurrency；set_presentation 仅替换展示阶段与分支文案，布局由前端计算，不用分组冒充执行循环。引用要在批次结束时完整；删除节点时同批显式修复相关连线/输入/条件/循环。不能使用数组下标或直接写状态。版本、幂等和只校验语义与 write_graph 相同；冲突先重读再决定新修改。',
    'workflow_inspect_run':'观察授权运行的真实节点、轮次、尝试、图版本、上游引用、错误和结构化结果。状态不代表副作用已回滚，不能依据正文猜测文件修改。用于本次运行重规划，不可任意查询其他运行。',
    'workflow_start':'在 Owner 已授予的启动范围内启动当前定义；先确保图可执行且指定所有任务角色，携带准确图版本。复用本次协调链和已消耗预算，重复调用不多建运行。仅设计请求没有此工具。',
    'workflow_control':'在本次明确授权运行内停止、继续或核对事实后重试准确尝试，使用 inspect_run 的最新进度 revision。不能代替人工确认、审批工具或隐式续额；图修改与运行控制分开。',
    'workflow_feedback_update':'处置本运行反馈。先 inspect_run 读取原意见及 feedback.revision；需要任务时先用 edit_graph 新增或调整未派发节点，再 assign 关联角色和节点。节点应通过 workflow_result 报告 feedback_resolved 布尔值。resolve 必须引用该处置节点真实完成的验证尝试，不能把新增节点、工具成功或文字承诺当作已解决；缺少能力时 wait 并说明需要的人类操作。不得代签人工确认或接受遗留。',
}
SCHEMAS={'workflow_read_graph':schemas.ReadGraph,'workflow_write_graph':schemas.WriteGraph,
    'workflow_edit_graph':schemas.EditGraph,'workflow_inspect_run':schemas.InspectRun,
    'workflow_start':schemas.StartGraph,'workflow_control':schemas.ManageRun,
    'workflow_feedback_update':FeedbackToolUpdate}


def specs(names):
    from ..agent.tool_definitions import tool_definition
    return [tool_definition(name, DESCRIPTIONS[name], SCHEMAS[name]) for name in names if name in SCHEMAS]


async def invoke(execution_id,name,body):
    from .planning import authorized
    from . import graph_service,service
    try:
        payload=SCHEMAS[name].model_validate(body)
        async with SessionLocal() as session:
            grant=await authorized(session,execution_id,name)
            cid,uid=grant.conversation_id,grant.owner_id
            kind,tid=('run',grant.run_id) if grant.run_id else ('definition',grant.definition_id)
        if name=='workflow_read_graph':
            result=await graph_service.read(cid,uid,kind,tid,execution_id=execution_id,**payload.model_dump())
        elif name in ('workflow_write_graph','workflow_edit_graph'):
            result=await graph_service.mutate(cid,uid,kind,tid,payload,execution_id=execution_id)
        elif name=='workflow_inspect_run':
            async with service.control_lock,SessionLocal() as session:
                current=await authorized(session,execution_id,name)
                run=await service.get_run(session,cid,uid,current.run_id)
                result=await service.run_json(session,run)
                from ..models import FileEffect,WorkflowAttempt
                from ..services.file_effects import decode
                from sqlalchemy import select
                evidence=[]
                attempts=list((await session.scalars(select(WorkflowAttempt).where(WorkflowAttempt.run_id==run.id))).all())
                for attempt in attempts:
                    if not attempt.execution_id: continue
                    rows=(await session.scalars(select(FileEffect).where(FileEffect.execution_id==attempt.execution_id))).all()
                    for row in rows:
                        item={'attempt_id':attempt.id,'execution_id':attempt.execution_id,'effect_id':row.id,'call_id':row.call_id,'item_index':row.item_index,'current_filesystem':'not_checked'}
                        try:
                            facts=decode(row)
                            item.update({k:facts[k] for k in ('operation','state','before_sha256','after_sha256','bytes','created_parent_count') if k in facts})
                        except Exception: item['state']='unknown'
                        evidence.append(item)
                allocation=await session.get(ExecutionAllocation,execution_id)
                allocation.authority_json={**allocation.authority_json,'inspected_attempts':[a.id for a in attempts if a.status in ('completed','failed','stopped','interrupted','blocked')],'inspected_revision':run.revision}
                await session.commit()
                result['file_evidence']=evidence
                result['evidence_scope']='仅原执行的最小持久证据，不授予工作区读取；缺失、过期或 prepared 均不能证明未执行。'
        elif name=='workflow_start':
            from .engine import start
            from .schemas import Start
            result=await start(cid,uid,Start(definition_id=tid,expected_revision=payload.expected_graph_revision,
                request_key='coord-'+grant.id,input_text=grant.goal,mode='coordinated',feedback_mode=grant.feedback_mode),manager_execution_id=execution_id)
        elif name=='workflow_feedback_update':
            from .feedback import update
            result=await update(cid,uid,grant.run_id,payload.feedback_id,
                FeedbackUpdate(**payload.model_dump(exclude={'feedback_id'})),execution_id=execution_id)
        else:
            from .schemas import Control
            result=await service.control(cid,uid,grant.run_id,Control(**payload.model_dump()),manager_execution_id=execution_id)
        return json.dumps(result,ensure_ascii=False,default=str)
    except HTTPException as exc:
        return REJECTED_OUTPUT_PREFIX+' '+json.dumps({'error':exc.detail if isinstance(exc.detail,dict) else {'code':str(exc.detail)}},ensure_ascii=False)
    except ValidationError as exc:
        return REJECTED_OUTPUT_PREFIX+' '+json.dumps({'error':{'code':'WORKFLOW_GRAPH_INVALID','fields':[list(e['loc']) for e in exc.errors(include_input=False,include_context=False)]}},ensure_ascii=False)


async def create(session,execution_id):
    allocation=await session.get(ExecutionAllocation,execution_id)
    result=[]
    for name in allocation.control_tools_json:
        if name not in SCHEMAS: continue
        def make_call(tool_name):
            async def call(**kwargs):
                return await invoke(execution_id,tool_name,kwargs)
            return call
        result.append(StructuredTool.from_function(coroutine=make_call(name),name=name,description=DESCRIPTIONS[name],
            args_schema=SCHEMAS[name],handle_validation_error=lambda _:REJECTED_OUTPUT_PREFIX+' WORKFLOW_GRAPH_INVALID'))
    return guard_tools(result,allow_dangerous=True)
