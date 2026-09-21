"""反馈只报告问题与证据，不改变工具权限或把业务结论伪装成执行终态。"""
from typing import Literal

from pydantic import Field, StrictBool, StrictInt

from .schemas import Strict


FeedbackCategory = Literal['implementation', 'contract', 'capability', 'unverified', 'suggestion']
FeedbackAction = Literal['assign', 'wait', 'review', 'resolve', 'dismiss', 'accept', 'obsolete', 'reopen', 'coordinate']


class FeedbackItem(Strict):
    """工作角色在自己结果中报告的单项意见，身份由工具宿主绑定。"""
    request_key: str = Field(min_length=1, max_length=64, pattern=r'^[a-zA-Z0-9_-]+$', description='本次问题的稳定幂等键；原请求重发沿用，修改内容使用新键。')
    category: FeedbackCategory = Field(description='实现缺陷、契约冲突、能力不足、未验证或建议；不能只用 approved=false 区分。')
    summary: str = Field(min_length=1, max_length=240, description='本项问题的简短标题，不包含凭据。')
    details: str = Field(default='', max_length=8000, description='问题依据与所需处置；实际来源节点由后台绑定，不用自然语言 @ 触发任务。')
    blocking: StrictBool = Field(default=True, description='问题未处理时暂停依赖本结果的后续节点；改进建议通常应设为 false。')
    requested_tools: list[str] = Field(default_factory=list, description='能力缺口所需工具名，仅供后台核对，不授予工具。')
    suggested_role_id: StrictInt | None = Field(default=None, description='建议处理的本群角色，可省略；不等于已派发。')


class CreateFeedback(FeedbackItem):
    """Owner 选择真实尝试提交反馈。"""
    attempt_id: str = Field(min_length=1, max_length=64)


class FeedbackDisposition(Strict):
    """人和协调者共用的版本、处理节点和证据字段，不包含 Owner 专属操作。"""
    expected_revision: StrictInt = Field(ge=1, description='读取反馈时取得的修订号；冲突后重新查看。')
    request_key: str = Field(min_length=1, max_length=64, pattern=r'^[a-zA-Z0-9_-]+$', description='本次处置的幂等键，重发保持不变。')
    reason: str = Field(min_length=1, max_length=4000, description='处置依据；接受遗留或人工核验需要具体理由。')
    handler_role_id: StrictInt | None = Field(default=None, description='assign 时使用的当前群处理角色。')
    handler_node_ids: list[str] = Field(default_factory=list, description='assign 时关联本运行真实的处理任务节点；这些节点可处理被反馈阻塞的问题。')
    verification_attempt_id: str | None = Field(default=None, max_length=64, description='resolve 时已完成的处理尝试，其结构化 feedback_resolved 必须为 true。')


class FeedbackUpdate(FeedbackDisposition):
    """Owner 处置有独立版本与请求键，人工核验必须明确记录。"""
    action: FeedbackAction = Field(description='assign 分配处理节点；wait 等待条件；review 等待复核；resolve 有证据地解决；coordinate 交给协调者。')
    manual_verification: StrictBool = Field(default=False, description='仅 Owner 可声明实际完成了人工核验；模型不能代签。')


class FeedbackToolUpdate(FeedbackDisposition):
    """协调工具只暴露当前授权范围内可用的操作，不提供代签字段。"""
    feedback_id: str = Field(min_length=1, max_length=64)
    action: Literal['assign', 'wait', 'review', 'resolve'] = Field(description='assign 关联处理节点；wait 等待条件；review 待复核；resolve 使用真实完成的验证尝试关闭。')
