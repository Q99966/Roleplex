"""有界多文件读取，独立授权与逐项事实，不提供写入事务或历史快照。"""
from __future__ import annotations

import asyncio
import json
from time import monotonic
from collections.abc import Awaitable, Callable
from dataclasses import asdict

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .files import WorkspaceFileError, WorkspaceFileService

MAX_BATCH_ITEMS = 8
MAX_BATCH_INPUT = 16384
MAX_BATCH_READ = 65536
MAX_BATCH_OUTPUT = 65536


class ReadItemInput(BaseModel):
    """每项独立相对路径及读取游标，未知参数不可进入文件层。"""
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True, strict=True)
    path: str = Field(min_length=1, max_length=1024, description="工作区相对文件路径。")
    offset_bytes: int = Field(default=0, ge=0, le=2**63 - 1, description="字节起始偏移，从0开始；行模式不要传。")
    max_bytes: int = Field(default=65536, ge=1, le=65536, description="字节模式的最大返回字节数；行模式不要传。")
    start_line: int | None = Field(default=None, ge=1, le=2**63 - 1, description="起始行，从1开始；与offset_bytes/max_bytes互斥。")
    end_line: int | None = Field(default=None, ge=1, le=2**63 - 1, description="包含结束行，省略默认200行；每项最多2000行。")
    expected_sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$', description="可选，之前读取或文本搜索返回的真实全文件版本。")

    @model_validator(mode='before')
    @classmethod
    def exclusive_range(cls, value):
        """Args:
            value：单节点原始参数；显式 null 不能冒充另一模式。
        """
        if isinstance(value, dict):
            if 'start_line' in value:
                if value['start_line'] is None or 'offset_bytes' in value or 'max_bytes' in value:
                    raise ValueError('WORKSPACE_READ_ARGUMENT_INVALID')
            elif 'end_line' in value:
                raise ValueError('WORKSPACE_READ_ARGUMENT_INVALID')
        return value

    @model_validator(mode='after')
    def bounded_range(self):
        """行范围包含两端，最多 2000 行。"""
        if self.start_line is not None and self.end_line is not None and not self.start_line <= self.end_line < self.start_line + 2000:
            raise ValueError('WORKSPACE_READ_ARGUMENT_INVALID')
        return self


class BatchReadInput(BaseModel):
    """统一读取工具 items 形式的内部校验，不接收工作区或执行身份。"""
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    items: list[ReadItemInput] = Field(min_length=1, max_length=MAX_BATCH_ITEMS)


def encode(value: dict) -> str:
    """Args:
        value：已经校验和限长的私有结果，仅用于模型与加密详情。
    """
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def validate_items(items: list) -> list[dict]:
    """执行前统一校验整批形态与字节预算，不将校验异常中的原参数带出。

    Args:
        items：模型请求或测试直接调用的逐文件输入。
    """
    try:
        value = BatchReadInput(items=items).model_dump(exclude_unset=True)
        size = len(encode(value).encode('utf-8'))
    except (ValidationError, UnicodeError, TypeError, ValueError):
        raise WorkspaceFileError('WORKSPACE_BATCH_ARGUMENT_INVALID') from None
    if size > MAX_BATCH_INPUT:
        raise WorkspaceFileError('WORKSPACE_BATCH_INPUT_TOO_LARGE',
            {'phase': 'precheck', 'actual': size, 'limit': MAX_BATCH_INPUT, 'unit': 'utf8_bytes'})
    return value['items']


class ReadBatchReceipt:
    """共享 generation 私有采集作用域内的逐项读取事实；不拥有线程或持久执行。"""

    def __init__(self, items: list[dict]):
        """Args:
            items：已通过整批 shape 和字节校验的输入。
        """
        self.value = {'version': 1, 'status': 'running', 'error_code': None, 'items': [
            {'id': f'item-{index}', 'operation': 'read', 'path': item['path'], 'status': 'pending',
             'error_code': None, 'output_limited': False, 'result': None}
            for index, item in enumerate(items)]}

    def release(self) -> None:
        """读取任务由 read_many 收尾，本凭据无外部资源。"""

    def export(self, output: dict | None) -> dict:
        """Args:
            output：模型普通结果不再复制；batch 自身已包含逐项有界结果。
        """
        return {'format': 'read-batch-v1', 'batch': self.value}


async def read_many(items: list, *, authorize: Callable[[], Awaitable[WorkspaceFileService | None]],
                    receipt: ReadBatchReceipt) -> str:
    """按实际结果依序分配内容/JSON 预算；整批共用一次准入和扫描计量。

    Args:
        items：模型请求，先全批预检。
        authorize：每项开始与返回前的实时权限检查。
        receipt：原调用的私有节点容器，取消保留已观察结果。
    """
    from ..config import settings
    from .scanning import ScanBudget
    from .scan_admission import admitted
    items = validate_items(items)
    remaining = settings.workspace_read_content_bytes
    try:
        async with admitted(len(encode({'items': items}).encode())):
            budget = ScanBudget()
            for node, item in zip(receipt.value['items'], items):
                if budget.scanned >= budget.limit or monotonic() >= budget.deadline:
                    node.update(status='budget_exhausted', error_code='WORKSPACE_SCAN_LIMIT_EXCEEDED')
                    continue
                result_budget = MAX_BATCH_OUTPUT - len(encode(receipt.value).encode()) - 1024
                if remaining < 1 or result_budget < 512:
                    node.update(status='budget_exhausted', error_code='WORKSPACE_READ_BUDGET_EXHAUSTED')
                    continue
                try:
                    node['status'] = 'running'
                    service = await authorize()
                    if service is None:
                        raise WorkspaceFileError('WORKSPACE_TOOL_NOT_AVAILABLE')
                    if 'start_line' in item:
                        result = await service.read_lines(item['path'], start_line=item['start_line'], end_line=item.get('end_line'),
                            expected_sha256=item.get('expected_sha256'), budget=budget, content_budget=remaining, json_budget=result_budget)
                        limited = result['limited_reason'] is not None
                    else:
                        requested = item.get('max_bytes', 65536)
                        result = asdict(await service.read(item['path'], offset_bytes=item.get('offset_bytes', 0),
                            max_bytes=min(requested, remaining), expected_sha256=item.get('expected_sha256'), budget=budget,
                            strict_budget=True, json_budget=result_budget))
                        limited = not result['eof'] and result['bytes'] < requested
                    node.update(status='success', result=result, output_limited=limited)
                    if await authorize() is None:
                        raise WorkspaceFileError('WORKSPACE_TOOL_NOT_AVAILABLE')
                    remaining -= result['bytes']
                except WorkspaceFileError as exc:
                    node.update(status='rejected' if exc.code == 'WORKSPACE_TOOL_NOT_AVAILABLE' else
                        'budget_exhausted' if exc.code == 'WORKSPACE_READ_BUDGET_EXHAUSTED' else 'failed',
                        error_code=exc.code, result=None)
                except asyncio.CancelledError:
                    if node['status'] == 'running':
                        node['status'] = 'cancelled'
                    raise
                except Exception:
                    node.update(status='failed', error_code='WORKSPACE_READ_FAILED', result=None)
        states = [node['status'] for node in receipt.value['items']]
        status = ('success' if all(state == 'success' for state in states) and not any(node['output_limited'] for node in receipt.value['items']) else
                  'partial' if 'success' in states else 'rejected' if all(state == 'rejected' for state in states) else 'failed')
        code = {'success': None, 'partial': 'WORKSPACE_BATCH_PARTIAL', 'failed': 'WORKSPACE_BATCH_FAILED',
                'rejected': 'WORKSPACE_TOOL_NOT_AVAILABLE'}[status]
        receipt.value.update(status=status, error_code=code)
    except WorkspaceFileError as exc:
        receipt.value.update(status='rejected', error_code=exc.code)
    except asyncio.CancelledError:
        receipt.value.update(status='cancelled', error_code=None)
        raise
    finally:
        for node in receipt.value['items']:
            if node['status'] in {'pending', 'running'}:
                node['status'] = 'not_executed' if node['status'] == 'pending' else 'cancelled'
    return encode(receipt.value)
