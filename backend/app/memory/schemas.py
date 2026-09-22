"""人和 Agent 共用的有界检索参数；角色/World/触发者由宿主绑定。"""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Search(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    query: str = Field(min_length=1, max_length=512, description='中文、英文或代码关键词；可组合多个词并继续改写查询。')
    scope: Literal['current', 'related'] = Field(default='current', description='当前会话，或当前角色可读取且允许向本会话共享的关联会话。')
    kinds: list[Literal['message', 'summary']] = Field(default=['message', 'summary'], min_length=1, max_length=2)
    limit: int = Field(default=6, ge=1, le=20)
    cursor: str | None = Field(default=None, max_length=2048)


class Read(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    reference: str = Field(min_length=1, max_length=256, description='搜索或会话摘要提供的来源引用；读取时重新检查权限和版本。')
    offset: int = Field(default=0, ge=0, le=2**31 - 1, description='正文 Unicode 字符偏移，从 0 开始。')
    max_characters: int = Field(default=4000, ge=1, le=12000)
    context_messages: int = Field(default=0, ge=0, le=2, description='需要时附带前后各若干条消息的有界片段。')
