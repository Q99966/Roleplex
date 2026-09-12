"""有界多文件读取，独立授权与逐项事实，不提供写入事务或历史快照。"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .files import WorkspaceFileError, WorkspaceFileService

MAX_BATCH_ITEMS = 8
MAX_BATCH_INPUT = 16384
MAX_BATCH_READ = 32768
MAX_BATCH_OUTPUT = 65536
_active_batches = 0


class ReadItemInput(BaseModel):
    """每项独立相对路径及读取游标，未知参数不可进入文件层。"""
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True, strict=True)
    path: str = Field(min_length=1, max_length=1024)
    offset_bytes: int = Field(default=0, ge=0, le=2**63 - 1)
    max_bytes: int = Field(default=4096, ge=1, le=32768)


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
        value = BatchReadInput(items=items).model_dump()
        size = len(encode(value).encode('utf-8'))
    except (ValidationError, UnicodeError, TypeError, ValueError):
        raise WorkspaceFileError('WORKSPACE_BATCH_ARGUMENT_INVALID') from None
    if size > MAX_BATCH_INPUT or sum(item['max_bytes'] for item in value['items']) > MAX_BATCH_READ:
        raise WorkspaceFileError('WORKSPACE_BATCH_INPUT_TOO_LARGE')
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


def _fit_node(node: dict, result: dict, offset: int, budget: int) -> None:
    """只缩短正文以适应 JSON 转义后的真实字节数，永不截坏 hash 或游标字段。

    Args:
        node：按固定输入顺序分配的私有节点。
        result：一次完整授权读取取得的结果。
        offset：本次请求的原始字节偏移。
        budget：本项含路径及元数据的 JSON 字节份额。
    """
    node.update(status='success', result=result)
    if len(encode(node).encode()) <= budget:
        return
    original = result['text']
    node['output_limited'] = True
    low, high = 0, len(original)
    while low < high:
        count = (low + high + 1) // 2
        text = original[:count]
        size = len(text.encode())
        node['result'] = {**result, 'text': text, 'bytes': size, 'next_offset': offset + size, 'eof': False}
        if len(encode(node).encode()) <= budget:
            low = count
        else:
            high = count - 1
    text = original[:low]
    size = len(text.encode())
    node['result'] = {**result, 'text': text, 'bytes': size, 'next_offset': offset + size, 'eof': False}


async def read_many(items: list, *, authorize: Callable[[], Awaitable[WorkspaceFileService | None]],
                    receipt: ReadBatchReceipt) -> str:
    """接纳最多两批、每批最多两项，错误相互隔离；取消时等待自身任务收尾。

    Args:
        items：校验后的请求，每项仍由既有文件服务检查路径/大小/编码。
        authorize：绑定宿主身份的实时权限复核，不接受模型覆盖。
        receipt：当前真实调用的私有采集容器，取消时保留已观察结果。
    """
    global _active_batches
    items = validate_items(items)
    if _active_batches >= 2:
        receipt.value.update(status='rejected', error_code='WORKSPACE_BATCH_BUSY')
        for node in receipt.value['items']:
            node['status'] = 'not_executed'
        return encode(receipt.value)
    # 在首次 await 前完成准入；等待任务只有已接纳批次内的至多八项，不建立无界队列。
    _active_batches += 1
    semaphore = asyncio.Semaphore(2)
    node_budget = (MAX_BATCH_OUTPUT - 256) // len(items)

    async def run_item(index: int, item: dict) -> None:
        """Args:
            index：固定子项序号，不按完成顺序排列。
            item：该子项的路径及游标。
        """
        node = receipt.value['items'][index]
        try:
            async with semaphore:
                node['status'] = 'running'
                service = await authorize()
                if service is None:
                    node.update(status='rejected', error_code='WORKSPACE_TOOL_NOT_AVAILABLE')
                    return
                result = await service.read(item['path'], offset_bytes=item['offset_bytes'], max_bytes=item['max_bytes'])
                _fit_node(node, asdict(result), item['offset_bytes'], node_budget)
        except asyncio.CancelledError:
            node['status'] = 'not_executed' if node['status'] == 'pending' else 'cancelled'
            raise
        except WorkspaceFileError as exc:
            node.update(status='failed', error_code=exc.code)
        except Exception:
            node.update(status='failed', error_code='WORKSPACE_READ_FAILED')

    tasks = [asyncio.create_task(run_item(index, item)) for index, item in enumerate(items)]
    try:
        await asyncio.gather(*tasks)
        states = [node['status'] for node in receipt.value['items']]
        status = ('success' if all(state == 'success' for state in states) else 'partial' if 'success' in states
                  else 'rejected' if all(state == 'rejected' for state in states) else 'failed')
        code = {'success': None, 'partial': 'WORKSPACE_BATCH_PARTIAL', 'failed': 'WORKSPACE_BATCH_FAILED',
                'rejected': 'WORKSPACE_TOOL_NOT_AVAILABLE'}[status]
        receipt.value.update(status=status, error_code=code)
        return encode(receipt.value)
    except asyncio.CancelledError:
        receipt.value.update(status='cancelled', error_code=None)
        raise
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            for node in receipt.value['items']:
                if node['status'] in {'pending', 'running'}:
                    node['status'] = 'not_executed' if node['status'] == 'pending' else 'cancelled'
            _active_batches -= 1
