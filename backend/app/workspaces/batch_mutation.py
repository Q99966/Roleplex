"""全批预检、顺序提交与有界私有差异，不提供跨文件事务或自动重放。"""
from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .files import WorkspaceFileError, WorkspaceFileService

INPUT_LIMIT = 256 * 1024
OUTPUT_LIMIT = 65536


class WriteItemInput(BaseModel):
    """单个创建/整文件替换项，不允许模型覆盖执行归属。"""
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True, strict=True)
    path: str = Field(min_length=1, max_length=1024)
    content: str = Field(max_length=1024 * 1024)
    expected_sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')


class EditItemInput(BaseModel):
    """单个唯一字面替换项，沿用 E1 字符/字节护栏。"""
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True, strict=True)
    path: str = Field(min_length=1, max_length=1024)
    old_text: str = Field(min_length=1, max_length=65536)
    new_text: str = Field(max_length=65536)
    expected_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


def encode(value: dict) -> str:
    """Args:
        value：只用于模型结果或加密业务详情的有界对象。
    """
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _reservation(item: dict) -> int:
    """Args:
        item：预留节点与差异文件各一份路径，以及结果/hash/状态元数据。
    """
    return 1536 + 2 * len(json.dumps(item['path'], ensure_ascii=False).encode())


def validate_items(operation: str, items: list) -> list[dict]:
    """在准入/写入前校验整批参数及元数据容量，不让成功结果无法表示。

    Args:
        operation：宿主工具绑定的 write/edit。
        items：待校验子项；错误不得回显原始源码。
    """
    if operation not in {'write', 'edit'} or not isinstance(items, list) or not 1 <= len(items) <= 8:
        raise WorkspaceFileError('WORKSPACE_BATCH_ARGUMENT_INVALID')
    schema = WriteItemInput if operation == 'write' else EditItemInput
    try:
        values = [schema.model_validate(item).model_dump() for item in items]
        size = len(encode({'items': values}).encode())
        reserved = sum(_reservation(item) for item in values)
    except (ValidationError, ValueError, TypeError, UnicodeError):
        raise WorkspaceFileError('WORKSPACE_BATCH_ARGUMENT_INVALID') from None
    if size > INPUT_LIMIT or reserved > OUTPUT_LIMIT // 2:
        raise WorkspaceFileError('WORKSPACE_BATCH_INPUT_TOO_LARGE')
    return values


class BatchMutationReceipt:
    """一个真实父调用的有界逐文件事实，仅保存当前项计算票据。"""

    def __init__(self, operation: str, items: list[dict]):
        """Args:
            operation：固定修改类型。
            items：已校验输入，不保存源码副本。
        """
        self.value = {'version': 1, 'status': 'running', 'error_code': None, 'items': [
            {'id': f'item-{index}', 'path': item['path'], 'operation': operation, 'status': 'not_executed',
             'applied': False, 'error_code': None, 'result': None, 'write': None, 'created_parent_count': 0} for index, item in enumerate(items)]}
        reservations = [_reservation(item) for item in items]
        spare = (OUTPUT_LIMIT - 512 - sum(reservations)) // len(items)
        self.budgets = [size + spare for size in reservations]
        self.line_budget = 1000 // len(items)
        self.current = None

    def retain(self, index: int) -> None:
        """采集失败只丢弃该项 diff，不改变提交结果或掩盖取消。

        Args:
            index：当前稳定子项索引。
        """
        if self.current is not None:
            self.value['items'][index]['created_parent_count'] = self.current.created_parent_count
        try:
            self._retain(index)
        except Exception:
            self.value['items'][index]['write'] = None

    def _retain(self, index: int) -> None:
        """将当前差异压入本项份额；缩减只影响展示，保留完整计算统计。

        Args:
            index：输入顺序的稳定子项。
        """
        if self.current is None:
            return
        node = self.value['items'][index]
        if node['applied'] is False:
            node['write'] = {'version': 1, 'availability': 'not_executed', 'reason': None, 'files': []}
            return
        original = self.current.value
        if node['applied'] is True and original['availability'] == 'result_unconfirmed':
            # 文件返回值已证明提交，只能说差异采集失败，不能反向抹去应用事实。
            original = {**original, 'availability': 'unavailable', 'reason': 'capture_failed'}
        count = sum(len(hunk['lines']) for file in original['files'] for hunk in file['hunks'])

        def prefix(keep: int) -> dict:
            """Args:
                keep：在文件/hunk 原顺序中保留的行数。
            """
            files = []
            for file in original['files']:
                hunks = []
                for hunk in file['hunks']:
                    lines = hunk['lines'][:keep]
                    keep -= len(lines)
                    if lines:
                        hunks.append({**hunk, 'lines': lines,
                            'old_lines': sum(line['kind'] != 'insert' for line in lines),
                            'new_lines': sum(line['kind'] != 'delete' for line in lines)})
                files.append({**file, 'hunks': hunks})
            return {**original, 'files': files}

        low, high = 0, min(count, self.line_budget)
        while low < high:
            middle = (low + high + 1) // 2
            value = prefix(middle)
            if len(encode({**node, 'write': value}).encode()) <= self.budgets[index]:
                low = middle
            else:
                high = middle - 1
        value = prefix(low)
        if low < count and value['availability'] in {'recorded', 'partial'}:
            value.update(availability='partial', reason=None)
        node['write'] = value
        if len(encode(node).encode()) > self.budgets[index]:
            # 容量预留按正常协议保证元数据可放入；异常采集只降级 diff，不抹掉已应用状态。
            node['write'] = None

    def release(self) -> None:
        """释放当前项票据，旧节点已不持有完整前后原文。"""
        if self.current is not None:
            self.current.release()
            self.current = None

    def export(self, output: dict | None) -> dict:
        """Args:
            output：模型结果不复制，已有 batch 节点包含有界结果。
        """
        return {'format': 'write-batch-v1', 'batch': self.value}


@asynccontextmanager
async def _locked(lock: asyncio.Lock):
    """Args:
        lock：既有工作区命令锁；只限制准入等待，不中断正在提交的同步文件操作。
    """
    from .write_admission import locked
    async with locked(lock):
        yield


async def _mutate_many(operation: str, items: list[dict], *, authorize: Callable[[], Awaitable[WorkspaceFileService | None]],
                      lock: asyncio.Lock, receipt: BatchMutationReceipt) -> str:
    """全批预检后顺序修改，不自动回滚或重试任何项。

    Args:
        operation：原工具绑定的 write/edit。
        items：批次输入，仍在边界校验。
        authorize：逐次复核真实 Owner/角色/租用/停服状态。
        lock：工作区既有命令串行锁。
        receipt：当前调用私有结果，正常取消也由消息所有者消费。
    """
    from ..agent.write_capture import WriteReceipt
    phase, index = 'precheck', 0
    node = receipt.value['items'][0]
    try:
        paths, inodes = set(), set()
        async with _locked(lock):
            for index, item in enumerate(items):
                node = receipt.value['items'][index]
                service = await authorize()
                if service is None:
                    raise WorkspaceFileError('WORKSPACE_TOOL_NOT_AVAILABLE')
                path, inode = await service.preflight(operation, item['path'], {k: v for k, v in item.items() if k != 'path'})
                if (path in paths or (inode is not None and inode in inodes)
                        or any(Path(path) in Path(other).parents or Path(other) in Path(path).parents for other in paths)):
                    raise WorkspaceFileError('WORKSPACE_BATCH_TARGET_CONFLICT')
                paths.add(path)
                if inode is not None:
                    inodes.add(inode)
        phase = 'apply'
        for index, item in enumerate(items):
            node = receipt.value['items'][index]
            receipt.current = WriteReceipt(item['path'])

            def applied(before: bytes | None, after: bytes) -> None:
                """Args:
                    before：文件层确认的实际旧字节。
                    after：已落地的新字节，先保存提交事实再尝试差异计算。
                """
                node.update(applied=True, status='success', result={'created': before is None,
                    'bytes': len(after), 'sha256': hashlib.sha256(after).hexdigest()})
                receipt.current.applied(before, after)

            async with _locked(lock):
                service = await authorize()
                if service is None:
                    raise WorkspaceFileError('WORKSPACE_TOOL_NOT_AVAILABLE')
                node.update(status='running', applied=None)
                result = await getattr(service, operation)(item['path'], **{k: v for k, v in item.items() if k != 'path'},
                    capture_applied=applied,
                    **({'capture_parent_created': receipt.current.parent_created} if operation == 'write' else {}))
                node.update(status='success', applied=True, result=asdict(result))
            # 计算和等待不得占用文件锁/命令锁；下一项重新授权，服务可能已经变化。
            try:
                await receipt.current.finish(encode(node['result']))
            except asyncio.CancelledError:
                raise
            except Exception:
                receipt.current.value.update(availability='unavailable', reason='capture_failed')
            receipt.retain(index)
            receipt.release()
        receipt.value.update(status='success')
    except asyncio.CancelledError:
        if node['status'] == 'running':
            node.update(status='result_unconfirmed', applied=None)
        if receipt.current is not None and node['applied']:
            receipt.current.value.update(availability='unavailable', reason='cancelled')
        receipt.value.update(status='cancelled')
        raise
    except (WorkspaceFileError, TimeoutError) as exc:
        code = exc.code if isinstance(exc, WorkspaceFileError) else 'WORKSPACE_BATCH_BUSY'
        if isinstance(exc, WorkspaceFileError) and exc.code == 'WORKSPACE_BATCH_BUSY':
            receipt.value['wait_diagnostic'] = exc.details
        if not node['applied']:
            node.update(status='failed', applied=False, error_code=code)
            from .diagnostics import AccessRejected
            if isinstance(exc, AccessRejected) and exc.diagnostic is not None:
                node['diagnostic'] = exc.diagnostic
        succeeded = any(item['applied'] for item in receipt.value['items'])
        receipt.value.update(status='rejected' if phase == 'precheck' else 'partial' if succeeded else 'failed',
            error_code='WORKSPACE_BATCH_PARTIAL' if succeeded else code)
    except Exception:
        if phase == 'apply' and node['status'] == 'running':
            node.update(status='result_unconfirmed', applied=None, error_code='WORKSPACE_BATCH_WRITE_UNCONFIRMED')
            receipt.value.update(status='result_unconfirmed', error_code='WORKSPACE_BATCH_WRITE_UNCONFIRMED')
        else:
            if not node['applied']:
                node.update(status='failed', applied=False, error_code='WORKSPACE_BATCH_PRECHECK_FAILED')
            succeeded = any(item['applied'] for item in receipt.value['items'])
            receipt.value.update(status='partial' if succeeded else 'rejected',
                error_code='WORKSPACE_BATCH_PARTIAL' if succeeded else 'WORKSPACE_BATCH_PRECHECK_FAILED')
    finally:
        try:
            receipt.retain(index)
        finally:
            receipt.release()
    return encode({**receipt.value, 'items': [{k: v for k, v in node.items() if k != 'write'} for node in receipt.value['items']]})


async def mutate_many(operation: str, items: list[dict], *, authorize: Callable[[], Awaitable[WorkspaceFileService | None]],
                      lock: asyncio.Lock, receipt: BatchMutationReceipt) -> str:
    """Args:
        operation：宿主绑定的 write/edit。
        items：整批参数，准入前验证。
        authorize：出队及每项提交前重新授权。
        lock：既有工作区共同锁。
        receipt：准入前已登记的调用凭据。
    """
    from .write_admission import admitted
    values = validate_items(operation, items)
    try:
        async with admitted(len(encode({'items': values}).encode())):
            return await _mutate_many(operation, values, authorize=authorize, lock=lock, receipt=receipt)
    except WorkspaceFileError as exc:
        if exc.code != 'WORKSPACE_BATCH_BUSY':
            raise
        receipt.value.update(status='rejected', error_code=exc.code, wait_diagnostic=exc.details)
        return encode({**receipt.value, 'items': [{k: v for k, v in node.items() if k != 'write'} for node in receipt.value['items']]})
    except asyncio.CancelledError:
        receipt.value['status'] = 'cancelled'
        raise
