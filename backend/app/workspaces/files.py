"""W1a 原生 UTF-8 文件列表、读取与乐观原子写。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .paths import WorkspacePathError, is_link_like, normalize_relative_path, resolve_workspace_path

MAX_FILE_BYTES = 1024 * 1024
MAX_READ_BYTES = 65_536
MAX_LIST_ITEMS = 200
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
        data = target.read_bytes()
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

    async def write(self, path: str, content: str, *, expected_sha256: str | None = None) -> WorkspaceWriteResult:
        """exclusive 新建或按 expected hash 原子替换 UTF-8 文件。"""
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_FILE_BYTES:
            raise WorkspaceFileError("WORKSPACE_FILE_TOO_LARGE")
        async with self._lock:
            try:
                target = resolve_workspace_path(self.root, path, require_exists=False)
            except WorkspacePathError as exc:
                if exc.code == "WORKSPACE_FILE_NOT_FOUND":
                    raise WorkspaceFileError("WORKSPACE_PARENT_NOT_FOUND") from None
                raise _translate(exc) from None
            parent = target.parent
            if not parent.is_dir():
                raise WorkspaceFileError("WORKSPACE_PARENT_NOT_FOUND")
            exists = target.exists() or target.is_symlink()
            if not exists:
                if expected_sha256 is not None:
                    raise WorkspaceFileError("WORKSPACE_FILE_REVISION_CONFLICT")
                try:
                    with target.open("xb") as handle:
                        handle.write(encoded)
                        handle.flush()
                        os.fsync(handle.fileno())
                except FileExistsError:
                    raise WorkspaceFileError("WORKSPACE_FILE_REVISION_CONFLICT") from None
                return WorkspaceWriteResult(created=True, bytes=len(encoded), sha256=_sha256(encoded))

            if target.is_symlink() or not target.is_file():
                raise WorkspaceFileError("WORKSPACE_PATH_OUTSIDE_ROOT")
            if target.stat().st_size > MAX_FILE_BYTES:
                raise WorkspaceFileError("WORKSPACE_FILE_TOO_LARGE")
            current = target.read_bytes()
            if expected_sha256 is None or _sha256(current) != expected_sha256:
                raise WorkspaceFileError("WORKSPACE_FILE_REVISION_CONFLICT")
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".roleplex-w1a-{self.execution_id[:12]}-", dir=parent,
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                # 临界点再次复核，避免同一进程外修改被静默覆盖。
                if _sha256(target.read_bytes()) != expected_sha256:
                    raise WorkspaceFileError("WORKSPACE_FILE_REVISION_CONFLICT")
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            return WorkspaceWriteResult(created=False, bytes=len(encoded), sha256=_sha256(encoded))

    @staticmethod
    def json_result(result: object) -> str:
        """把类型化结果编码成稳定紧凑 JSON，供工具返回模型。"""
        return json.dumps(asdict(result), ensure_ascii=False, separators=(",", ":"))
