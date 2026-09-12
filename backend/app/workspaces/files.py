"""W1a 原生 UTF-8 文件列表、读取与乐观原子写。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from collections.abc import Callable

from .paths import WorkspacePathError, is_link_like, normalize_relative_path, resolve_workspace_path

MAX_FILE_BYTES = 1024 * 1024
MAX_READ_BYTES = 65_536
MAX_LIST_ITEMS = 200
MAX_EDIT_BYTES = 65536
_WRITE_LOCKS: dict[str, asyncio.Lock] = {}


class WorkspaceFileError(ValueError):
    """携带稳定错误码的原生文件工具失败。"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class WorkspaceReadResult:
    """一次 UTF-8 分块读取结果。"""

    text: str
    bytes: int
    eof: bool
    next_offset: int
    sha256: str


@dataclass(frozen=True)
class WorkspaceWriteResult:
    """一次新建或替换结果。"""

    created: bool
    bytes: int
    sha256: str


@dataclass(frozen=True)
class WorkspaceListResult:
    """稳定排序的目录页。"""

    items: list[dict[str, Any]]
    truncated: bool
    next_after_name: str | None


def _sha256(content: bytes) -> str:
    """计算内容身份，不把原文带入日志。"""
    return hashlib.sha256(content).hexdigest()


def _translate(exc: WorkspacePathError) -> WorkspaceFileError:
    """保持路径层稳定错误码并转换为文件服务异常。"""
    return WorkspaceFileError(exc.code)


def _capture_applied(callback: Callable | None, before: bytes | None, after: bytes) -> None:
    """观察器失败不能改变已经提交的文件结果；观察器自行维护不可用状态。

    Args:
        callback：内部私有采集函数，不能读取额外宿主内容。
        before：旧版本，创建为 None。
        after：提交的新版本。
    """
    if callback is not None:
        try:
            callback(before, after)
        except Exception:
            # 不持久化观察器异常对象，它可能持有文件原文。
            pass


class WorkspaceFileService:
    """绑定一个已授权 execution 与规范工作区根的原生文件服务。"""

    def __init__(self, *, root: Path, execution_id: str):
        self.root = root.resolve(strict=True)
        self.execution_id = execution_id
        self._lock = _WRITE_LOCKS.setdefault(str(self.root), asyncio.Lock())

    def _resolve(self, path: str, *, allow_root: bool = False, require_exists: bool = True) -> Path:
        """在绑定根内解析路径，并转换稳定异常类型。"""
        try:
            return resolve_workspace_path(
                self.root, path, allow_root=allow_root, require_exists=require_exists,
            )
        except WorkspacePathError as exc:
            raise _translate(exc) from None

    async def list(self, path: str = ".", *, after_name: str | None = None, limit: int = 200) -> WorkspaceListResult:
        """列出目录一页；symlink 只报告身份，不读取目标。"""
        if isinstance(limit, bool) or limit < 1 or limit > MAX_LIST_ITEMS:
            raise WorkspaceFileError("WORKSPACE_PATH_INVALID")
        directory = self._resolve(path, allow_root=True)
        if not directory.is_dir():
            raise WorkspaceFileError("WORKSPACE_DIRECTORY_NOT_FOUND")
        with os.scandir(directory) as entries:
            names = []
            for entry in entries:
                try:
                    normalize_relative_path(entry.name)
                except WorkspacePathError:
                    # 敏感或跨平台非法名称不能作为工具结果发送给模型。
                    continue
                names.append(entry.name)
        names.sort(key=lambda name: name.encode("utf-8"))
        if after_name is not None:
            names = [name for name in names if name.encode("utf-8") > after_name.encode("utf-8")]
        selected = names[:limit]
        items: list[dict[str, Any]] = []
        for name in selected:
            candidate = directory / name
            if is_link_like(candidate):
                items.append({"name": name, "type": "symlink", "size": None})
            elif candidate.is_dir():
                items.append({"name": name, "type": "directory", "size": None})
            elif candidate.is_file():
                items.append({"name": name, "type": "file", "size": candidate.stat().st_size})
            else:
                items.append({"name": name, "type": "other", "size": None})
        truncated = len(names) > limit
        return WorkspaceListResult(
            items=items,
            truncated=truncated,
            next_after_name=selected[-1] if truncated and selected else None,
        )

    async def read(self, path: str, *, offset_bytes: int = 0, max_bytes: int = 65_536) -> WorkspaceReadResult:
        """读取 UTF-8 普通文件的一段，并返回全文件 hash。"""
        if isinstance(offset_bytes, bool) or offset_bytes < 0:
            raise WorkspaceFileError("WORKSPACE_PATH_INVALID")
        if isinstance(max_bytes, bool) or max_bytes < 1 or max_bytes > MAX_READ_BYTES:
            raise WorkspaceFileError("WORKSPACE_PATH_INVALID")
        target = self._resolve(path)
        if not target.is_file():
            raise WorkspaceFileError("WORKSPACE_FILE_NOT_TEXT")
        if target.stat().st_size > MAX_FILE_BYTES:
            raise WorkspaceFileError("WORKSPACE_FILE_TOO_LARGE")
        # stat 后外部程序可能扩写；读取本身也必须有界，批量调用不能放大瞬时分配。
        with target.open('rb') as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise WorkspaceFileError("WORKSPACE_FILE_TOO_LARGE")
        try:
            data.decode("utf-8")
            data[:offset_bytes].decode("utf-8")
        except UnicodeDecodeError:
            raise WorkspaceFileError("WORKSPACE_FILE_NOT_TEXT") from None
        if offset_bytes > len(data):
            raise WorkspaceFileError("WORKSPACE_PATH_INVALID")
        if offset_bytes == len(data):
            return WorkspaceReadResult(
                text="", bytes=0, eof=True, next_offset=offset_bytes, sha256=_sha256(data),
            )
        requested_end = min(len(data), offset_bytes + max_bytes)
        end = requested_end
        while end > offset_bytes:
            try:
                text = data[offset_bytes:end].decode("utf-8")
                break
            except UnicodeDecodeError:
                end -= 1
        else:
            # max_bytes 可能小于下一个 UTF-8 字符；向前最多补齐 3 字节，保证游标始终推进。
            text = ""
            for candidate_end in range(requested_end + 1, min(len(data), requested_end + 3) + 1):
                try:
                    text = data[offset_bytes:candidate_end].decode("utf-8")
                except UnicodeDecodeError:
                    continue
                end = candidate_end
                break
            if not text:
                raise WorkspaceFileError("WORKSPACE_FILE_NOT_TEXT") from None
        return WorkspaceReadResult(
            text=text,
            bytes=end - offset_bytes,
            eof=end == len(data),
            next_offset=end,
            sha256=_sha256(data),
        )

    async def write(self, path: str, content: str, *, expected_sha256: str | None = None,
                    capture_applied: Callable[[bytes | None, bytes], None] | None = None) -> WorkspaceWriteResult:
        """新建或按 hash 替换；只在确认提交后交出本次前后版本。

        Args:
            path：授权工作区内相对路径。
            content：新 UTF-8 内容。
            expected_sha256：更新时必需的全文件旧 hash。
            capture_applied：内部私有观察器，只能同步预留，不能等待计算或执行额外写入。
        """
        async with self._lock:
            target, current, encoded = self._prepare_write(path, content, expected_sha256)
            if current is None:
                try:
                    with target.open("xb") as handle:
                        handle.write(encoded)
                        handle.flush()
                        os.fsync(handle.fileno())
                except FileExistsError:
                    raise WorkspaceFileError("WORKSPACE_FILE_REVISION_CONFLICT") from None
                _capture_applied(capture_applied, None, encoded)
                return WorkspaceWriteResult(created=True, bytes=len(encoded), sha256=_sha256(encoded))

            return self._replace_existing(path, current, encoded, capture_applied)

    def _prepare_write(self, path: str, content: str, expected_sha256: str | None) -> tuple[Path, bytes | None, bytes]:
        """单文件提交与批量预检共用写前规则，调用者持有文件锁。

        Args:
            path：工作区相对目标。
            content：完整待写内容。
            expected_sha256：创建为空、替换为旧版本。
        """
        encoded = content.encode('utf-8')
        if len(encoded) > MAX_FILE_BYTES:
            raise WorkspaceFileError('WORKSPACE_FILE_TOO_LARGE')
        try:
            target = resolve_workspace_path(self.root, path, require_exists=False)
        except WorkspacePathError as exc:
            if exc.code == 'WORKSPACE_FILE_NOT_FOUND':
                raise WorkspaceFileError('WORKSPACE_PARENT_NOT_FOUND') from None
            raise _translate(exc) from None
        if not target.parent.is_dir():
            raise WorkspaceFileError('WORKSPACE_PARENT_NOT_FOUND')
        if not target.exists():
            if expected_sha256 is not None:
                raise WorkspaceFileError('WORKSPACE_FILE_REVISION_CONFLICT')
            return target, None, encoded
        return target, self._read_update(path, expected_sha256), encoded

    def _read_update(self, path: str, expected_sha256: str | None) -> bytes:
        """持锁读取待更新文件并校验完整版本；再次解析防止复用过期路径对象。

        Args:
            path：授权根内的相对目标。
            expected_sha256：读取工具给出的全文件版本。
        """
        target = self._resolve(path)
        if is_link_like(target) or not target.is_file():
            raise WorkspaceFileError('WORKSPACE_PATH_OUTSIDE_ROOT')
        if target.stat().st_size > MAX_FILE_BYTES:
            raise WorkspaceFileError('WORKSPACE_FILE_TOO_LARGE')
        with target.open('rb') as handle:
            current = handle.read(MAX_FILE_BYTES + 1)
        if len(current) > MAX_FILE_BYTES:
            raise WorkspaceFileError('WORKSPACE_FILE_TOO_LARGE')
        if expected_sha256 is None or _sha256(current) != expected_sha256:
            raise WorkspaceFileError('WORKSPACE_FILE_REVISION_CONFLICT')
        return current

    def _replace_existing(self, path: str, current: bytes, encoded: bytes, capture_applied: Callable | None) -> WorkspaceWriteResult:
        """write/edit 共用唯一原子替换路径，调用者持有工作区锁。

        Args:
            path：当前授权目标。
            current：同次校验的旧版本字节。
            encoded：待提交的精确新字节。
            capture_applied：成功后接收实际前后版本的私有观察器。
        """
        if len(encoded) > MAX_FILE_BYTES:
            raise WorkspaceFileError('WORKSPACE_FILE_TOO_LARGE')
        target = self._resolve(path)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f'.roleplex-w1a-{self.execution_id[:12]}-', dir=target.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, 'wb') as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            # 不在匹配和提交之间释放锁；外部变更仍需在提交边界重新检查，非 OS 级 CAS。
            self._read_update(path, _sha256(current))
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        _capture_applied(capture_applied, current, encoded)
        return WorkspaceWriteResult(created=False, bytes=len(encoded), sha256=_sha256(encoded))

    async def edit(self, path: str, old_text: str, new_text: str, *, expected_sha256: str,
                   capture_applied: Callable[[bytes | None, bytes], None] | None = None) -> WorkspaceWriteResult:
        """唯一字面匹配后修改已有 UTF-8 文件，不解释正则或自动补全上下文。

        Args:
            path：授权根内已有普通文件。
            old_text：非空、必须唯一匹配的旧片段。
            new_text：替换片段，可为空但不删除文件。
            expected_sha256：本次读取的完整文件 hash，必填。
            capture_applied：成功提交后共享 D 差异采集。
        """
        async with self._lock:
            current, encoded = self._prepare_edit(path, old_text, new_text, expected_sha256)
            return self._replace_existing(path, current, encoded, capture_applied)

    def _prepare_edit(self, path: str, old_text: str, new_text: str, expected_sha256: str) -> tuple[bytes, bytes]:
        """单文件提交与批量预检共用精确替换规则，不把预检内容作为未来提交快照。

        Args:
            path：已有文件路径。
            old_text：唯一旧片段。
            new_text：新片段。
            expected_sha256：完整旧版本。
        """
        if not isinstance(old_text, str) or not old_text or not isinstance(new_text, str) or not isinstance(expected_sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_sha256):
            raise WorkspaceFileError('WORKSPACE_EDIT_ARGUMENT_INVALID')
        try:
            size = len(old_text.encode('utf-8')) + len(new_text.encode('utf-8'))
        except UnicodeEncodeError:
            raise WorkspaceFileError('WORKSPACE_EDIT_ARGUMENT_INVALID') from None
        if size > MAX_EDIT_BYTES:
            raise WorkspaceFileError('WORKSPACE_EDIT_INPUT_TOO_LARGE')
        current = self._read_update(path, expected_sha256)
        try:
            text = current.decode('utf-8')
        except UnicodeDecodeError:
            raise WorkspaceFileError('WORKSPACE_FILE_NOT_TEXT') from None
        index = text.find(old_text)
        if index < 0:
            raise WorkspaceFileError('WORKSPACE_EDIT_MATCH_NOT_FOUND')
        # 从下一字符寻找第二处，以免 str.count 的非重叠语义漏掉 aaa 中两处 aa。
        if text.find(old_text, index + 1) >= 0:
            raise WorkspaceFileError('WORKSPACE_EDIT_MATCH_AMBIGUOUS')
        encoded = (text[:index] + new_text + text[index + len(old_text):]).encode('utf-8')
        if len(encoded) > MAX_FILE_BYTES:
            raise WorkspaceFileError('WORKSPACE_FILE_TOO_LARGE')
        return current, encoded

    async def preflight(self, operation: str, path: str, arguments: dict) -> tuple[str, tuple[int, int] | None]:
        """无写入地验证一项并返回别名检测身份，提交时仍必须重做校验。

        Args:
            operation：宿主固定的 write/edit，不来自子项参数。
            path：本项相对路径。
            arguments：通过对应 schema 的内容与版本。
        """
        async with self._lock:
            if operation == 'write':
                target, _, _ = self._prepare_write(path, **arguments)
            elif operation == 'edit':
                self._prepare_edit(path, **arguments)
                target = self._resolve(path)
            else:
                raise WorkspaceFileError('WORKSPACE_BATCH_ARGUMENT_INVALID')
            stat = target.stat() if target.exists() else None
            identity = (stat.st_dev, stat.st_ino) if stat and stat.st_ino else None
            return os.path.normcase(str(target)), identity

    @staticmethod
    def json_result(result: object) -> str:
        """把类型化结果编码成稳定紧凑 JSON，供工具返回模型。"""
        return json.dumps(asdict(result), ensure_ascii=False, separators=(",", ":"))
