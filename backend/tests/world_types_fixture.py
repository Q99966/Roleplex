"""受控世界类型装配，仅测试入口导入，不由运行配置或存档加载。"""
from pydantic import BaseModel, ConfigDict, Field

from app.world_types import Initialization, WorldMaterial, WorldTypeDescriptor, register
from app.world_types.contracts import ActivityPlan, WorldActivityDefinition, WorldToolDefinition, EmptyConfiguration


class Configuration(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    label: str = Field(default='', max_length=100, title='样例名称')
    model_config_id: int | None = Field(default=None, gt=0, title='类型使用的模型', json_schema_extra={'widget': 'model_config'})


async def initialize(session, ctx, config, resources):
    from app.db import now_utc
    from app.models import ModelConfig, Conversation, ConversationMember, Role
    model = await session.get(ModelConfig, config['model_config_id']) if config['model_config_id'] else None
    missing = []
    if not config['label']:
        missing.append('label')
    if model is None or model.created_by != ctx.owner_id:
        missing.append('model_config_id')
    if missing:
        return Initialization(status='needs_configuration', resources=resources, required_fields=tuple(missing))
    if not resources:
        role = Role(created_by=ctx.owner_id, name='类型验证助手', system_prompt='受控世界类型验证角色。',
            model_config_id=model.id, model_name='fake-model', builtin_tools_json=['type_fixture_record'], created_at=now_utc(), updated_at=now_utc())
        session.add(role); await session.flush()
        conversation = Conversation(type='single', title=config['label'], created_by=ctx.owner_id, created_at=now_utc())
        session.add(conversation); await session.flush()
        session.add_all([ConversationMember(conversation_id=conversation.id, member_type=kind, member_id=mid, joined_at=now_utc())
            for kind, mid in [('role', role.id), ('user', ctx.owner_id)]])
        resources = {'role_id': role.id, 'conversation_id': conversation.id}
        coordinator = Role(created_by=ctx.owner_id, name='类型活动协调者', system_prompt='受控活动协调角色。',
            model_config_id=model.id, model_name='fake-model', created_at=now_utc(), updated_at=now_utc())
        session.add(coordinator); await session.flush()
        group = Conversation(type='group', title='受控类型活动群', created_by=ctx.owner_id, created_at=now_utc(),
            orchestrator_enabled=True, orchestrator_role_id=coordinator.id, orchestrator_revision=1)
        session.add(group); await session.flush()
        session.add_all([ConversationMember(conversation_id=group.id, member_type=kind, member_id=mid, joined_at=now_utc())
            for kind, mid in [('user', ctx.owner_id), ('role', role.id), ('role', coordinator.id)]])
        from uuid import uuid4
        from app.models import WorkflowDefinition
        definition = WorkflowDefinition(id=uuid4().hex, conversation_id=group.id, name='受控类型活动', revision=1,
            graph={'runtime_version': 2, 'nodes': [{'id': 'work', 'kind': 'role', 'title': '受控类型工具', 'role_id': role.id,
                'task': '[WORLD_TYPE_TOOL_PROBE]', 'tools': ['type_fixture_record']}], 'edges': []},
            created_at=now_utc(), updated_at=now_utc())
        session.add(definition)
        resources.update(activity_group_id=group.id, definition_id=definition.id, coordinator_role_id=coordinator.id)
    return Initialization(resources=resources)


async def overview(session, ctx, config, resources):
    return {'label': config['label'], 'resource_count': len(resources)}


async def build_context(session, ctx, config):
    from app.models import WorldTypeState
    row = await session.get(WorldTypeState, 1)
    if ctx.conversation_id != row.resources_json.get('conversation_id'):
        return []
    return [WorldMaterial(source_id='fixture-configuration', revision=row.revision, text='TYPE_FIXTURE_CONTEXT ' + config['label'])]


async def validate_sources(session, ctx, receipt):
    from app.models import WorldTypeState
    row = await session.get(WorldTypeState, 1)
    return row is not None and all(source['source_id'] == 'fixture-configuration' and source['revision'] == row.revision for source in receipt['sources'])


async def tools(session, ctx, config):
    from app.models import WorldTypeState
    row = await session.get(WorldTypeState, 1)
    if ctx.role_id != row.resources_json.get('role_id'):
        return []
    async def record(actor, parameters, configuration):
        from app.db import SessionLocal
        async with SessionLocal() as write:
            state = await write.get(WorldTypeState, 1)
            state.resources_json = {**state.resources_json, 'tool_execution_id': actor.execution_id,
                'tool_task_id': actor.world_task_id, 'recorded': True}
            await write.commit()
        return {'recorded': True, 'world_task_id': actor.world_task_id}
    return [WorldToolDefinition(name='type_fixture_record', description='将本次受控类型活动的真实执行身份写入类型状态。',
        args_model=EmptyConfiguration, execute=record)]


async def prepare_activity(session, ctx, parameters, config, resources):
    return ActivityPlan(conversation_id=resources['activity_group_id'], definition_id=resources['definition_id'],
        graph_revision=1, goal='执行受控类型活动并交付工具结果。', reference={'kind': 'fixture-record'})


def install():
    register(WorldTypeDescriptor(id='fixture', version=1, name='受控扩展类型', description='只供隔离验收使用的类型接入固件。',
        frontend_entry='fixture', configuration_model=Configuration, initializer=initialize, overview=overview,
        build_context=build_context, validate_sources=validate_sources, resolve_tools=tools,
        activities=(WorldActivityDefinition(name='record', description='运行受控类型的记录活动。', parameters_model=EmptyConfiguration, prepare=prepare_activity),)))
