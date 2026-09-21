"""可保存通用有向图；运行能力在启动边界单独校验。"""
from typing import Literal
from pydantic_core import PydanticCustomError
from pydantic import BaseModel, ConfigDict, Field, model_validator, StrictBool, StrictInt, StrictFloat, StrictStr, ValidationError


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Position(Strict):
    """仅保存画布坐标；拒绝非有限值，位置不授予执行顺序或权限。"""
    x: float = Field(allow_inf_nan=False, strict=True)
    y: float = Field(allow_inf_nan=False, strict=True)


Scalar = StrictBool | StrictInt | StrictFloat | StrictStr | None


class Condition(Strict):
    """受控标量比较，不支持脚本或从自然语言猜测判断。"""
    sources: list[str] = Field(min_length=1)
    key: str = Field(pattern=r'^[A-Za-z_][A-Za-z0-9_]*$', max_length=64)
    operator: Literal['eq', 'ne'] = 'eq'
    value: Scalar = True
    aggregate: Literal['all', 'any'] = 'all'


class EdgeRule(Strict):
    source: str
    target: str
    when: Literal['always', 'true', 'false'] = 'always'


class Loop(Strict):
    """显式循环域，不为旧图自动猜测回边及出口。"""
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]+$', min_length=1, max_length=64)
    entry: str
    decision: str
    exit: str
    body: list[str] = Field(min_length=1)
    repeat_when: bool = False
    max_iterations: int | None = Field(default=None, ge=1)
    carry_inputs: list[str] = Field(default_factory=list)


class Node(Strict):
    id: str = Field(min_length=1, max_length=64, pattern=r'^[a-zA-Z0-9_-]+$')
    kind: Literal['role', 'approval', 'join', 'condition', 'judge']
    title: str = Field(min_length=1, max_length=128)
    role_id: int | None = Field(default=None,description='本群成员的角色 ID，由 read_graph.members 获取。角色任务启动前必须指定；非模型节点为空，协调判断使用当前协调者。')
    task: str = Field(default='',description='仅本节点的具体任务；草稿可暂缺，模型节点启动前必须补齐，不修改角色全局 system prompt。')
    expected_output: str = ''
    inputs: list[str] = Field(default_factory=list,description='明确消费哪些上游节点结果；独立于控制连线，不包含任意私有工具详情。')
    tools: list[str] | None = Field(default=None,description='本任务原生工具名；只可从所选成员当前能力取子集。[] 不授予原生工具，null 按当前授权初始化，不开启全局开关。')
    result_keys: list[str] = Field(default_factory=list)
    result_schema: dict[str, Literal['boolean', 'integer', 'number', 'string', 'null']] = Field(default_factory=dict)
    condition: Condition | None = None
    position: Position | None = None
    color: str | None = Field(default=None, pattern=r'^#[0-9a-fA-F]{6}$')


class DisplayGroup(Strict):
    """展示阶段只引用真实节点；分组不创建执行、依赖或权限。"""
    id: str = Field(min_length=1, max_length=64, pattern=r'^[a-zA-Z0-9_-]+$')
    title: str = Field(min_length=1, max_length=80, pattern=r'.*\S.*')
    node_ids: list[str] = Field(min_length=1)


class EdgeLabel(Strict):
    """业务文案独立于 when/condition，不能用文案改变分支选择。"""
    source: str
    target: str
    label: str = Field(min_length=1, max_length=80, pattern=r'.*\S.*')


class Presentation(Strict):
    """兼容的图展示元数据；坐标继续使用已有 node.position。"""
    groups: list[DisplayGroup] = Field(default_factory=list)
    edge_labels: list[EdgeLabel] = Field(default_factory=list)


class Graph(Strict):
    nodes: list[Node] = Field(min_length=1)
    edges: list[tuple[str, str]]
    runtime_version: Literal[1, 2] = 1
    entries: list[str] = Field(default_factory=list)
    edge_rules: list[EdgeRule] = Field(default_factory=list)
    loops: list[Loop] = Field(default_factory=list)
    concurrency: int | None = Field(default=None, ge=1)
    presentation: Presentation | None = Field(default=None, description='仅展示阶段和连线文案，不改变执行关系；节点坐标由前端布局，阶段不能替代业务循环声明。')

    def validate_presentation(self):
        """展示引用同样局限于本图，返回安全字段定位而不回显正文。"""
        if not self.presentation: return
        ids = {node.id for node in self.nodes}
        seen_groups, seen_labels = set(), set()
        def invalid(loc):
            raise ValidationError.from_exception_data('Graph', [{'type': PydanticCustomError('workflow_graph_invalid', 'WORKFLOW_GRAPH_INVALID'),
                'loc': ('presentation', *loc), 'input': None}])
        for index, group in enumerate(self.presentation.groups):
            if group.id in seen_groups: invalid(('groups', index, 'id'))
            seen_groups.add(group.id)
            if len(set(group.node_ids)) != len(group.node_ids) or not set(group.node_ids).issubset(ids): invalid(('groups', index, 'node_ids'))
        for index, label in enumerate(self.presentation.edge_labels):
            pair = (label.source, label.target)
            if pair not in self.edges or pair in seen_labels: invalid(('edge_labels', index))
            seen_labels.add(pair)

    @model_validator(mode='after')
    def valid_graph(self):
        """保存允许分支/环路，但拒绝未知端点、重复 ID/连线及失效输入引用。"""
        self.validate_presentation()
        ids = [node.id for node in self.nodes]
        if len(set(ids)) != len(ids) or len(set(self.edges)) != len(self.edges) or any(
            source not in ids or target not in ids for source, target in self.edges
        ):
            raise PydanticCustomError('workflow_path_invalid', 'WORKFLOW_PATH_INVALID')
        for node in self.nodes:
            if not node.title.strip() or (node.kind in ('role', 'judge') and ((self.runtime_version == 1 and not node.role_id) or not node.task.strip())):
                raise PydanticCustomError('workflow_node_invalid', 'WORKFLOW_NODE_INVALID')
            if node.kind in ('approval', 'join', 'condition') and node.role_id is not None:
                raise PydanticCustomError('workflow_node_invalid', 'WORKFLOW_NODE_INVALID')
            if len(set(node.inputs)) != len(node.inputs) or not set(node.inputs).issubset(ids):
                raise PydanticCustomError('workflow_input_invalid', 'WORKFLOW_INPUT_INVALID')
        return self


class Save(Strict):
    name: str = Field(min_length=1, max_length=128)
    graph: Graph
    expected_revision: int = Field(ge=0)


class Start(Strict):
    definition_id: str
    expected_revision: int = Field(ge=0)
    request_key: str = Field(min_length=1, max_length=64)
    input_text: str = ''
    mode: Literal['manual', 'coordinated'] = 'manual'
    feedback_mode: Literal['manual', 'automatic'] = Field(default='manual', description='协调执行可授权自动处置节点反馈；使用原链预算，不扩大成员或工具权限。')


class Control(Strict):
    expected_revision: int = Field(ge=0)
    action: Literal['confirm', 'stop', 'retry', 'resume']
    attempt_id: str | None = None
    instruction: str = ''
    decision: bool = True
    acknowledge_facts: bool = False
    rerun_downstream: bool = False


def serial_execution_graph(graph: dict) -> dict:
    """冻结前检查唯一串行路径，节点位置不参与执行顺序。

    仅编辑保存分支/环路，不为当前执行器猜测分支条件或循环终止；拒绝发生在创建消息和预算之前。
    """
    from fastapi import HTTPException
    if any(node['kind'] not in ('role', 'approval') or node.get('tools') is not None for node in graph['nodes']) or graph.get('loops') or graph.get('edge_rules'):
        raise HTTPException(422, 'WORKFLOW_VERSION_REQUIRED')
    nodes = {node['id']: node for node in graph['nodes']}
    edges = graph['edges']
    next_node = {}
    incoming = set()
    for source, target in edges:
        if source not in nodes or target not in nodes or source in next_node or target in incoming:
            raise HTTPException(422, 'WORKFLOW_EXECUTION_UNSUPPORTED')
        next_node[source] = target
        incoming.add(target)
    roots = set(nodes) - incoming
    if len(edges) != len(nodes) - 1 or len(roots) != 1:
        raise HTTPException(422, 'WORKFLOW_EXECUTION_UNSUPPORTED')
    cursor = next(iter(roots))
    ordered = []
    seen = set()
    while cursor is not None and cursor not in seen:
        if not set(nodes[cursor]['inputs']).issubset(seen):
            raise HTTPException(422, 'WORKFLOW_INPUT_UNAVAILABLE')
        ordered.append(nodes[cursor])
        seen.add(cursor)
        cursor = next_node.get(cursor)
    if len(ordered) != len(nodes):
        raise HTTPException(422, 'WORKFLOW_EXECUTION_UNSUPPORTED')
    return {'nodes': ordered, 'edges': [[a['id'], b['id']] for a, b in zip(ordered, ordered[1:])]}
