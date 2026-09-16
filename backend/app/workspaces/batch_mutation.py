"""全批预检、顺序提交与有界私有差异，不提供跨文件事务或自动重放。"""
from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .files import WorkspaceFileError, WorkspaceFileService
from .replacements import ReplacementInput, MAX_REPLACEMENTS, validate_edit_shape

DIFF_OUTPUT_LIMIT = 65536


class WriteItemInput(BaseModel):
    """单个创建/整文件替换项，不允许模型覆盖执行归属。"""
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True, strict=True)
    path: str = Field(min_length=1, max_length=1024, description="工作区相对文件路径，自动创建缺失父目录。")
    content: str = Field(max_length=1024 * 1024, description="完整UTF-8正文，最终文件最多1 MiB。")
    expected_sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$', description="新建时省略；覆盖时填最近读取返回的真实sha256。")


class EditItemInput(BaseModel):
    """单个文件编辑节点，兼容单片段与原始版本上的多片段替换。"""
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True, strict=True)
    path: str = Field(min_length=1, max_length=1024, description="已有UTF-8文件的相对路径。")
    old_text: str | None = Field(default=None, min_length=1, max_length=65536, description="非空且唯一匹配的旧片段；与replacements互斥。")
    new_text: str | None = Field(default=None, max_length=65536, description="替换片段，可为空字符串；与replacements互斥。")
    replacements: list[ReplacementInput] | None = Field(default=None, min_length=1, max_length=MAX_REPLACEMENTS, description="基于同一原始版本的1..32处不重叠替换；本文件全部新旧片段合计最多64 KiB。")
    expected_sha256: str = Field(pattern=r'^[a-f0-9]{64}$', description="必填，最近读取返回的真实全文件sha256。")

    @model_validator(mode='before')
    @classmethod
    def exclusive_fragments(cls, value):
        """Args:
            value：按原始字段判断旧/新形式互斥。
        """
        return validate_edit_shape(value)


def encode(value: dict) -> str:
    """Args:
        value：只用于模型结果或加密业务详情的有界对象。
    """
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def validate_items(operation: str, items: list) -> list[dict]:
    """在准入/写入前校验每项形态；不按批次项数或总JSON大小拒绝。

    Args:
        operation：宿主工具绑定的 write/edit。
        items：待校验子项；错误不得回显原始源码。
    """
    if operation not in {'write', 'edit'} or not isinstance(items, list) or not items:
        raise WorkspaceFileError('WORKSPACE_BATCH_ARGUMENT_INVALID')
    schema = WriteItemInput if operation == 'write' else EditItemInput
    try:
        values = [schema.model_validate(item).model_dump(exclude_none=operation == 'edit') for item in items]
        # 保留UTF-8形态校验，逐项检查，不以整批编码长度拒绝。
        for item in values:
            encode(item).encode('utf-8')
    except (ValidationError, ValueError, TypeError, UnicodeError):
        raise WorkspaceFileError('WORKSPACE_BATCH_ARGUMENT_INVALID') from None
    return values


class BatchMutationReceipt:
    """一个真实父调用的完整逐文件事实；只有差异展示预算有界。"""

    def __init__(self, operation: str, items: list[dict]):
        """Args:
            operation：固定修改类型。
            items：已校验输入，不保存源码副本。
        """
        self.value = {'version': 1, 'status': 'running', 'error_code': None, 'items': [
            {'id': f'item-{index}', 'path': item['path'], 'operation': operation, 'status': 'not_executed',
             'applied': False, 'error_code': None, 'result': None, 'write': None, 'created_parent_count': 0} for index, item in enumerate(items)]}
        # 提交事实始终完整保存，差异独立分享预算，不能倒逼限制输入项数。
        self.budgets = [DIFF_OUTPUT_LIMIT // len(items)] * len(items)
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
            value = {'version': 1, 'availability': 'not_executed', 'reason': None, 'files': []}
            node['write'] = value if len(encode(value).encode()) <= self.budgets[index] else None
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
            if len(encode(value).encode()) <= self.budgets[index]:
                low = middle
            else:
                high = middle - 1
        value = prefix(low)
        if low < count and value['availability'] in {'recorded', 'partial'}:
            value.update(availability='partial', reason=None)
        node['write'] = value
        if len(encode(value).encode()) > self.budgets[index]:
            # 差异元数据本身也可能放不下；只省略 diff，完整提交事实仍留在节点。
            node['write'] = None

    def release(self) -> None:
        """释放当前项票据，旧节点已不持有完整前后原文。"""
        if self.current is not None:
            self.current.release()
            self.current = None

    def export(self, output: dict | None) -> dict:
        """Args:
            output：模型结果不复制，batch 保留完整逐项事实与限额内差异。
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
        paths, ancestors, inodes = set(), set(), set()
        async with _locked(lock):
            for index, item in enumerate(items):
                node = receipt.value['items'][index]
                service = await authorize()
                if service is None:
                    raise WorkspaceFileError('WORKSPACE_TOOL_NOT_AVAILABLE')
                path, inode = await service.preflight(operation, item['path'], {k: v for k, v in item.items() if k != 'path'})
                parents = {str(parent) for parent in Path(path).parents}
                # 按路径深度查祖先，避免取消项数限制后全批两两比较。
                if (path in paths or path in ancestors or bool(parents & paths)
                        or (inode is not None and inode in inodes)):
                    raise WorkspaceFileError('WORKSPACE_BATCH_TARGET_CONFLICT')
                paths.add(path)
                ancestors.update(parents)
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
                    capture_applied=applied, _effect_index=index, _recheck=authorize,
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
        if isinstance(exc, WorkspaceFileError) and exc.details and 'replacement_index' in exc.details:
            node['edit_error'] = exc.details
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
