"""可保存通用有向图；运行能力在启动边界单独校验。"""
from typing import Literal
from pydantic_core import PydanticCustomError
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Position(Strict):
    """仅保存画布坐标；拒绝非有限值，位置不授予执行顺序或权限。"""
    x: float = Field(allow_inf_nan=False, strict=True)
    y: float = Field(allow_inf_nan=False, strict=True)


class Node(Strict):
    id: str = Field(min_length=1, max_length=64, pattern=r'^[a-zA-Z0-9_-]+$')
    kind: Literal['role', 'approval']
    title: str = Field(min_length=1, max_length=128)
    role_id: int | None = None
    task: str = ''
    expected_output: str = ''
    inputs: list[str] = Field(default_factory=list)
    position: Position | None = None
    color: str | None = Field(default=None, pattern=r'^#[0-9a-fA-F]{6}$')


class Graph(Strict):
    nodes: list[Node] = Field(min_length=1)
    edges: list[tuple[str, str]]

    @model_validator(mode='after')
    def valid_graph(self):
        """保存允许分支/环路，但拒绝未知端点、重复 ID/连线及失效输入引用。"""
        ids = [node.id for node in self.nodes]
        if len(set(ids)) != len(ids) or len(set(self.edges)) != len(self.edges) or any(
            source not in ids or target not in ids for source, target in self.edges
        ):
            raise PydanticCustomError('workflow_path_invalid', 'WORKFLOW_PATH_INVALID')
        for node in self.nodes:
            if not node.title.strip() or (node.kind == 'role' and (not node.role_id or not node.task.strip())):
                raise PydanticCustomError('workflow_node_invalid', 'WORKFLOW_NODE_INVALID')
            if node.kind == 'approval' and node.role_id is not None:
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


class Control(Strict):
    expected_revision: int = Field(ge=0)
    action: Literal['confirm', 'stop', 'retry', 'resume']
    attempt_id: str | None = None
    instruction: str = ''
    acknowledge_facts: bool = False
    rerun_downstream: bool = False


def serial_execution_graph(graph: dict) -> dict:
    """冻结前检查唯一串行路径，节点位置不参与执行顺序。

    仅编辑保存分支/环路，不为当前执行器猜测分支条件或循环终止；拒绝发生在创建消息和预算之前。
    """
    from fastapi import HTTPException
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
