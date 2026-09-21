"""模型、画布与 REST 共用的图修改契约；ID 操作与整图写入分开。"""
from typing import Annotated, Literal
from pydantic import Field, model_validator, ValidationError
from pydantic_core import PydanticCustomError
from .schemas import Strict, Graph, Node, Position, Condition, Loop


class DraftCondition(Condition):
    sources: list[str] = Field(default_factory=list)
    key: str = Field(default='approved',pattern=r'^(?:[A-Za-z_][A-Za-z0-9_]*)?$',max_length=64)


class DraftLoop(Loop):
    entry: str = ''
    decision: str = ''
    exit: str = ''
    body: list[str] = Field(default_factory=list)


class DraftNode(Node):
    condition: DraftCondition | None = None


class DraftGraph(Graph):
    """草稿允许无节点/空任务；结构引用仍必须完整，执行校验在发布边界进行。"""
    nodes: list[DraftNode] = Field(default_factory=list)
    loops: list[DraftLoop] = Field(default_factory=list)
    edges: list[tuple[str, str]] = Field(default_factory=list)
    runtime_version: Literal[1, 2] = 2

    @model_validator(mode='after')
    def valid_graph(self):
        def invalid(code,loc):
            raise ValidationError.from_exception_data('DraftGraph',[{'type':PydanticCustomError('workflow_graph_invalid',code),'loc':tuple(loc),'input':None}])
        ids=[node.id for node in self.nodes]
        for index,nid in enumerate(ids):
            if nid.startswith('__') or nid in ids[:index]: invalid('WORKFLOW_NODE_INVALID',['nodes',index,'id'])
        for index,edge in enumerate(self.edges):
            if edge in self.edges[:index]: invalid('WORKFLOW_PATH_INVALID',['edges',index])
            for endpoint,nid in enumerate(edge):
                if nid not in ids: invalid('WORKFLOW_PATH_INVALID',['edges',index,endpoint])
        for index,nid in enumerate(self.entries):
            if nid not in ids or nid in self.entries[:index]: invalid('WORKFLOW_INPUT_INVALID',['entries',index])
        rules=[]
        for index,rule in enumerate(self.edge_rules):
            pair=(rule.source,rule.target)
            if pair not in self.edges or pair in rules: invalid('WORKFLOW_EDGE_CONFIG_INVALID',['edge_rules',index])
            rules.append(pair)
        for index,node in enumerate(self.nodes):
            if not node.title.strip(): invalid('WORKFLOW_NODE_INVALID',['nodes',index,'title'])
            if node.kind in ('approval','join','condition') and node.role_id is not None: invalid('WORKFLOW_NODE_INVALID',['nodes',index,'role_id'])
            for offset,nid in enumerate(node.inputs):
                if nid not in ids or nid in node.inputs[:offset]: invalid('WORKFLOW_INPUT_INVALID',['nodes',index,'inputs',offset])
            if node.condition:
                for offset,nid in enumerate(node.condition.sources):
                    if nid!='$self' and nid not in ids: invalid('WORKFLOW_INPUT_INVALID',['nodes',index,'condition','sources',offset])
        seen=[]
        for index,loop in enumerate(self.loops):
            if loop.id in seen: invalid('WORKFLOW_LOOP_CONFIG_INVALID',['loops',index,'id'])
            seen.append(loop.id)
            for field in ['entry','decision','exit']:
                if getattr(loop,field) and getattr(loop,field) not in ids: invalid('WORKFLOW_LOOP_CONFIG_INVALID',['loops',index,field])
            for field in ['body','carry_inputs']:
                for offset,nid in enumerate(getattr(loop,field)):
                    if nid not in ids: invalid('WORKFLOW_LOOP_CONFIG_INVALID',['loops',index,field,offset])
        if self.runtime_version==1:
            if not self.nodes: invalid('WORKFLOW_NODE_INVALID',['nodes'])
            Graph.valid_graph(self)
        return self


class NodePatch(Strict):
    title: str | None = Field(default=None, min_length=1, max_length=128)
    kind: Literal['role','approval','join','condition','judge'] | None = None
    role_id: int | None = None
    task: str | None = None
    expected_output: str | None = None
    tools: list[str] | None = None
    result_keys: list[str] | None = None
    result_schema: dict[str, Literal['boolean','integer','number','string','null']] | None = None
    position: Position | None = None
    color: str | None = Field(default=None, pattern=r'^#[0-9a-fA-F]{6}$')


class AddNode(Strict):
    op: Literal['add_node']
    node: DraftNode = Field(description='新节点；id 在当前图唯一，角色 ID 来自 read_graph 返回的本群成员。')


class UpdateNode(Strict):
    op: Literal['update_node']
    node_id: str = Field(description='要修改的稳定节点 ID，不能使用数组下标。')
    changes: NodePatch = Field(description='仅列出要改的设计字段；不可写入运行身份或执行状态。')


class RemoveNode(Strict):
    op: Literal['remove_node']
    node_id: str


class Connect(Strict):
    op: Literal['connect']
    source: str
    target: str
    when: Literal['always','true','false'] = 'always'


class Disconnect(Strict):
    op: Literal['disconnect']
    source: str
    target: str


class SetInputs(Strict):
    op: Literal['set_inputs']
    node_id: str
    inputs: list[str] = Field(description='本节点明确消费的上游节点 ID；不等同于连接控制边。')


class SetCondition(Strict):
    op: Literal['set_condition']
    node_id: str
    condition: DraftCondition | None


class UpsertLoop(Strict):
    op: Literal['upsert_loop']
    loop: DraftLoop


class RemoveLoop(Strict):
    op: Literal['remove_loop']
    loop_id: str


class SetEntries(Strict):
    op: Literal['set_entries']
    entries: list[str]


class SetConcurrency(Strict):
    op: Literal['set_concurrency']
    concurrency: int | None = Field(ge=1)


Operation = Annotated[AddNode | UpdateNode | RemoveNode | Connect | Disconnect | SetInputs | SetCondition | UpsertLoop | RemoveLoop | SetEntries | SetConcurrency, Field(discriminator='op')]


class Mutation(Strict):
    expected_graph_revision: int = Field(ge=0, description='read_graph 返回的目标图版本；0 只表示授权的新草稿尚不存在。')
    mutation_key: str = Field(min_length=1, max_length=64, description='持久幂等键；同一修改重发保持不变，修改内容后使用新键。')
    validate_only: bool = Field(default=False, description='true 只检查、不创建定义或修改图；正式提交仍重新复核版本和权限。')


class WriteGraph(Mutation):
    graph: DraftGraph = Field(description='完整目标图。遗漏已有业务节点表示删除；既有节点省略坐标/颜色时保留原展示值。不可把部分读图响应当整图覆盖。')
    name: str | None = Field(default=None, min_length=1, max_length=128, description='可选流程名称；省略保留当前名称，新草稿默认“协调流程”。')


class EditGraph(Mutation):
    operations: list[Operation] = Field(min_length=1, description='按稳定 ID 的原子操作批次，最终引用须完整；任一步失败整批不提交。')


class ReadGraph(Strict):
    node_ids: list[str] | None = Field(default=None, description='可选节点 ID，返回其直接关联子图；省略读取完整图。部分响应不能直接整图替换。')
    graph_revision: int | None = Field(default=None, ge=0, description='可选历史图版本；省略为最新目标图。历史版本只读。')


class InspectRun(Strict):
    pass


class StartGraph(Strict):
    expected_graph_revision: int = Field(ge=1, description='即将启动的定义图版本；保存并不隐式启动，只有执行授权才有此工具。')


class ManageRun(Strict):
    action: Literal['stop','retry','resume'] = Field(description='仅控制本次授权运行；不含人工确认和工具审批。')
    expected_revision: int = Field(ge=0, description='inspect_run 返回的运行进度 revision。')
    attempt_id: str | None = None
    instruction: str = ''
    acknowledge_facts: bool = Field(default=False,description='重试前须实际 inspect_run 核对本尝试；未知写入证据需要 Owner 人工核对，不能自称已经确认。')
    rerun_downstream: bool = False


class Coordinate(Strict):
    role_id: int = Field(description='由 @ 选择器解析的协调角色 ID，必须等于本群当前任命。')
    mode: Literal['design','execute','replan'] = 'design'
    goal: str = Field(min_length=1, description='Owner 的规划或调整要求，不改变本次授予能力。')
    request_key: str = Field(min_length=1, max_length=64)
    definition_id: str | None = None
    run_id: str | None = None
    expected_graph_revision: int | None = Field(default=None, ge=0)
    continue_session_id: str | None = None
    protected_nodes: list[str] | None = Field(default=None, description='Owner 明确固定的节点及其连接/循环结构；发送前展示保护范围。')


class CancelCoordination(Strict):
    expected_revision: int = Field(ge=0)


class SavedGraph(DraftGraph):
    runtime_version: Literal[1,2] = 1


class SaveDraft(Strict):
    name: str = Field(min_length=1,max_length=128)
    graph: SavedGraph
    expected_revision: int = Field(ge=0)
