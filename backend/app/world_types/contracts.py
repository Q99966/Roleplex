"""世界类型接入契约；宿主构造身份，扩展负责自己的业务材料与版本验证。"""
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field


class EmptyConfiguration(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


@dataclass(frozen=True)
class WorldContext:
    """仅宿主构造；模型参数和浏览器请求不能覆盖执行者或当前世界。"""
    world_name: str
    world_type: str
    type_version: int
    owner_id: int
    user_id: int
    role_id: int | None = None
    conversation_id: int | None = None
    execution_id: str | None = None
    execution_kind: str | None = None
    through_message_id: int | None = None
    parent_execution_id: str | None = None
    world_task_id: str | None = None
    budget_chain_id: str | None = None
    root_budget_chain_id: str | None = None
    appointment_revision: int | None = None


@dataclass(frozen=True)
class Initialization:
    """初始化仅操作宿主短事务中的业务资源，不调用模型/外部命令或自行 commit。"""
    status: Literal['ready', 'needs_configuration'] = 'ready'
    resources: dict[str, Any] = field(default_factory=dict)
    required_fields: tuple[str, ...] = ()


class WorldMaterial(BaseModel):
    """只进入本次输入的有来源材料，不同步到普通会话的共享摘要。"""
    model_config = ConfigDict(extra='forbid', strict=True)
    source_id: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=0)
    text: str = Field(max_length=100_000)


@dataclass(frozen=True)
class WorldToolDefinition:
    """受信类型的工具；权限仍须由角色显式开启及本次分配允许，默认 dangerous。

    execute 接收可信 WorldContext、经过 args_model 校验的参数与配置；不持有宿主长事务。
    """
    name: str
    description: str
    args_model: type[BaseModel]
    execute: Callable[..., Awaitable[dict]]


@dataclass(frozen=True)
class ActivityPlan:
    """类型活动交给既有群工作流执行，精确版本与类型自己的引用一并保存。"""
    conversation_id: int
    definition_id: str
    graph_revision: int
    goal: str
    reference: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorldActivityDefinition:
    name: str
    description: str
    parameters_model: type[BaseModel]
    # 在宿主短事务中准备业务状态/图；实际模型执行由宿主派发，不在这里调用 Provider。
    prepare: Callable[..., Awaitable[ActivityPlan]]


@dataclass(frozen=True)
class WorldTypeDescriptor:
    """扩展函数收到 AsyncSession、可信 WorldContext 及已验证的配置。

    initializer 的末参数为该类型上次资源引用；材料验证收到无正文的来源凭据。
    注册是受信代码操作，不是 Owner/模型的任意代码上传能力。
    """
    id: str
    version: int
    name: str
    description: str = ''
    frontend_entry: str = 'general'
    configuration_model: type[BaseModel] = EmptyConfiguration
    initializer: Callable[..., Awaitable[Initialization]] | None = None
    overview: Callable[..., Awaitable[dict]] | None = None
    build_context: Callable[..., Awaitable[list[WorldMaterial]]] | None = None
    validate_sources: Callable[..., Awaitable[bool]] | None = None
    resolve_tools: Callable[..., Awaitable[list[WorldToolDefinition]]] | None = None
    activities: tuple[WorldActivityDefinition, ...] = ()

    def view(self):
        return {'id': self.id, 'version': self.version, 'name': self.name, 'description': self.description,
            'frontend_entry': self.frontend_entry, 'configuration_schema': self.configuration_model.model_json_schema(),
            'capabilities': {'initialization': self.initializer is not None, 'context': self.build_context is not None,
                'overview': self.overview is not None, 'tools': self.resolve_tools is not None, 'activities': bool(self.activities)},
            'activities': [{'name': a.name, 'description': a.description, 'parameters_schema': a.parameters_model.model_json_schema()} for a in self.activities]}
