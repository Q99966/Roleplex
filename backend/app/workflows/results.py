"""每节点结果契约同时驱动工具 schema 和提交验证，不靠正文推断类型。"""
from pydantic import BaseModel, ConfigDict, Field, create_model, StrictBool, StrictInt, StrictFloat, StrictStr
from .schemas import Scalar
from .feedback_schemas import FeedbackItem

TYPES={'boolean':StrictBool,'integer':StrictInt,'number':StrictInt|StrictFloat,'string':StrictStr,'null':type(None)}


def expected_type(value):
    return 'boolean' if type(value) is bool else 'integer' if type(value) is int else 'number' if type(value) is float else 'null' if value is None else 'string'


def contract(graph,node):
    fields={key:None for key in node.get('result_keys',[])}
    fields.update(node.get('result_schema',{}))
    for consumer in graph.get('nodes',[]):
        condition=consumer.get('condition')
        if condition and condition.get('key') and (node['id'] in condition['sources'] or (node['id']==consumer['id'] and '$self' in condition['sources'])):
            key=condition['key'];kind=expected_type(condition['value'])
            if fields.get(key) not in (None,kind) and {fields[key],kind}!={'integer','number'}:
                from .graph_service import problem
                problem('WORKFLOW_RESULT_SCHEMA_CONFLICT',node_id=node['id'],fields=[key])
            fields[key]=fields.get(key) or kind
    return fields


class Values(BaseModel):
    model_config=ConfigDict(extra='allow',allow_inf_nan=False)
    __pydantic_extra__: dict[str,Scalar]=Field(init=False)


def schema(fields):
    # 内部字段名不使用模型/用户提供的 key，避免覆盖 BaseModel 方法；JSON 使用真实别名。
    values=create_model('WorkflowNodeValues',__base__=Values,**{
        'field_'+str(index):(TYPES[kind] if kind else Scalar,Field(alias=key,description='本节点必须报告的结构化结果；类型由定义和下游条件确定。'))
        for index,(key,kind) in enumerate(sorted(fields.items()))})
    return create_model('WorkflowNodeResult',__config__=ConfigDict(extra='forbid',allow_inf_nan=False),
        expected_result_revision=(StrictInt|None,Field(default=None,ge=0,description='首次报告可省略或传 0；修正已接受但尚未交接的报告，必须传上次工具返回的 result_revision。')) ,
        values=(values,Field(description='结构化字段；条件只消费这些值，不读取正文中的“通过”。可附加其他标量依据。')),
        summary=(str,Field(default='',description='简述判断依据；不能伪造文件操作或上游结果。')),
        feedback=(list[FeedbackItem],Field(default_factory=list,max_length=20,description='需要后续处置的意见；按实现、契约、能力缺口、未验证或建议分类。没有问题传空列表，不为正常完成制造反馈。')))
