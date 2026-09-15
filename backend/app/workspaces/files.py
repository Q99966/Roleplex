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

from .replacements import MAX_FRAGMENT_BYTES
from .paths import WorkspacePathError, is_link_like, normalize_relative_path, resolve_workspace_path

MAX_FILE_BYTES = 1024 * 1024
MAX_READ_BYTES = 65_536
MAX_LIST_ITEMS = 200
MAX_EDIT_BYTES = MAX_FRAGMENT_BYTES
_WRITE_LOCKS: dict[str, asyncio.Lock] = {}


class WorkspaceFileError(ValueError):
    """携带稳定错误码的原生文件工具失败。"""

    def __init__(self, code: str, details: dict | None = None):
        """Args:
            code：稳定错误码。
            details：执行层批准的安全预算数字，不放入异常文本。
        """
        super().__init__(code)
        self.code = code
        self.details = details


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
        except (OSError, RuntimeError):
            raise WorkspaceFileError('WORKSPACE_PATH_INVALID') from None

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

    async def read(self, path: str, *, offset_bytes: int = 0, max_bytes: int = 65536,
                   expected_sha256: str | None = None, budget=None, strict_budget: bool = False,
                   json_budget: int = 65536) -> WorkspaceReadResult:
        """增量扫描完整版本，仅保留有界片段；旧五字段响应保持不变。

        Args:
            path：绑定根内的 UTF-8 普通文件。
            offset_bytes：本次字节起点。
            max_bytes：期望返回额度，仍受主机预算控制。
            expected_sha256：可选全文件版本约束。
            budget：批次共享扫描计量。
            strict_budget：不允许为了补齐字符超出批次剩余量。
            json_budget：完整结果可占用的 JSON 字节数。
        """
        from .scan_admission import admitted
        from .scanning import read_bytes
        async with admitted():
            return WorkspaceReadResult(**await read_bytes(self, path, offset_bytes=offset_bytes, max_bytes=max_bytes,
                expected_sha256=expected_sha256, budget=budget, strict_budget=strict_budget, json_budget=json_budget))

    async def read_lines(self, path: str, *, start_line: int, end_line: int | None = None,
                         expected_sha256: str | None = None, budget=None, content_budget=None,
                         json_budget: int = 65536) -> dict:
        """按实际行范围读取，返回全文件 hash 和可继续的下一行。

        Args:
            path：绑定根内路径。
            start_line：包含的起始行，从 1 开始。
            end_line：包含的结束行，省略最多 200 行。
            expected_sha256：已知完整版本，不接受片段 hash。
            budget：共享扫描预算。
            content_budget：本次剩余内容额度。
            json_budget：本项完整结果额度。
        """
        from .scan_admission import admitted
        from .scanning import read_lines
        async with admitted():
            return await read_lines(self, path, start_line=start_line, end_line=end_line, expected_sha256=expected_sha256,
                budget=budget, content_budget=content_budget, json_budget=json_budget)

    async def search(self, *, query: str | None = None, mode: str = 'text', path: str = '.', limit: int = 100,
                     context_lines: int = 1, queries: list[str] | None = None, match: str = 'any', authorize=None) -> dict:
        """在绑定根内搜索，路径和查询不解释为 Shell。

        Args:
            query：单个字面文本或文件名模式。
            queries：多个字面词，与 query 互斥。
            match：any/all，同一行的匹配条件。
            mode：text/files。
            path：相对扫描范围。
            limit：结果数量上限。
            context_lines：匹配行前后片段数。
            authorize：工厂的实时授权复核回调。
        """
        from .scan_admission import admitted
        from .scanning import search
        async with admitted():
            return await search(self, query=query, mode=mode, path=path, limit=limit,
                                context_lines=context_lines, queries=queries, match=match, authorize=authorize)

    async def write(self, path: str, content: str, *, expected_sha256: str | None = None,
                    capture_applied: Callable[[bytes | None, bytes], None] | None = None,
                    capture_parent_created: Callable[[], None] | None = None) -> WorkspaceWriteResult:
        """新建或按 hash 替换；只在确认提交后交出本次前后版本。

        Args:
            path：授权工作区内相对路径。
            content：新 UTF-8 内容。
            capture_parent_created：每个父目录成功创建时同步记录计数，不包含路径。
            expected_sha256：更新时必需的全文件旧 hash。
            capture_applied：内部私有观察器，只能同步预留，不能等待计算或执行额外写入。
        """
        from .write_admission import locked
        async with locked(self._lock):
            target, current, encoded = self._prepare_write(path, content, expected_sha256)
            if current is None:
                self._create_write_parents(path, capture_parent_created=capture_parent_created)
                # 目录创建不等于文件提交；外部新建目标必须重新走版本检查。
                target, current, encoded = self._prepare_write(path, content, expected_sha256)
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

    def _write_target(self, path: str) -> Path:
        """无副作用检查全部已有祖先；缺失后缀只作为待创建路径返回。

        Args:
            path：绑定根内待写相对路径，先整体检查敏感组件与深度。
        """
        try:
            normalized = normalize_relative_path(path)
        except WorkspacePathError as exc:
            raise _translate(exc) from None
        if is_link_like(self.root) or not self.root.is_dir():
            raise WorkspaceFileError('WORKSPACE_PATH_OUTSIDE_ROOT')
        parts = normalized.split('/')
        for index in range(len(parts)):
            target = self._resolve('/'.join(parts[:index + 1]), require_exists=False)
            if not target.exists():
                return self.root / normalized
            if index < len(parts) - 1 and not target.is_dir():
                raise WorkspaceFileError('WORKSPACE_PARENT_NOT_FOUND')
        return target

    def _create_write_parents(self, path: str, *, capture_parent_created: Callable[[], None] | None = None) -> None:
        """逐层创建缺失目录并复核链接；失败不删除已创建目录或外部内容。

        Args:
            path：已经整体预检的文件路径，仅在实际写入阶段调用。
            capture_parent_created：宿主的同步计数回调，创建成功后立即调用。
        """
        self._write_target(path)
        parts = path.split('/')[:-1]
        for index in range(len(parts)):
            relative = '/'.join(parts[:index + 1])
            parent = self._resolve(relative, require_exists=False)
            try:
                parent.mkdir()
            except FileExistsError:
                pass
            except OSError:
                raise WorkspaceFileError('WORKSPACE_PARENT_NOT_FOUND') from None
            else:
                if capture_parent_created is not None:
                    capture_parent_created()
            # 已存在不代表可信目录，仍需拒绝被替换的 symlink/junction。
            if not self._resolve(relative).is_dir():
                raise WorkspaceFileError('WORKSPACE_PARENT_NOT_FOUND')

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
        target = self._write_target(path)
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

    async def edit(self, path: str, old_text: str | None = None, new_text: str | None = None, *, expected_sha256: str,
                   replacements: list[dict] | None = None,
                   capture_applied: Callable[[bytes | None, bytes], None] | None = None) -> WorkspaceWriteResult:
        """唯一字面匹配后修改已有 UTF-8 文件，不解释正则或自动补全上下文。

        Args:
            path：授权根内已有普通文件。
            replacements：多个基于同一原始版本的片段，与旧字段互斥。
            old_text：非空、必须唯一匹配的旧片段。
            new_text：替换片段，可为空但不删除文件。
            expected_sha256：本次读取的完整文件 hash，必填。
            capture_applied：成功提交后共享 D 差异采集。
        """
        from .write_admission import locked
        async with locked(self._lock):
            current, encoded = self._prepare_edit(path, old_text, new_text, expected_sha256, replacements=replacements)
            return self._replace_existing(path, current, encoded, capture_applied)

    def _prepare_edit(self, path: str, old_text: str | None = None, new_text: str | None = None, expected_sha256: str | None = None, *, replacements: list[dict] | None = None) -> tuple[bytes, bytes]:
        """单文件提交与批量预检共用精确替换规则，不把预检内容作为未来提交快照。

        Args:
            path：已有文件路径。
            replacements：可选多片段输入。
            old_text：唯一旧片段。
            new_text：新片段。
            expected_sha256：完整旧版本。
        """
        from .replacements import normalize_pairs, apply_replacements
        if not isinstance(expected_sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_sha256):
            raise WorkspaceFileError('WORKSPACE_EDIT_ARGUMENT_INVALID')
        pairs = normalize_pairs(old_text, new_text, replacements)
        current = self._read_update(path, expected_sha256)
        return current, apply_replacements(current, pairs, indexed=replacements is not None)

    async def preflight(self, operation: str, path: str, arguments: dict) -> tuple[str, tuple[int, int] | None]:
        """无写入地验证一项并返回别名检测身份，提交时仍必须重做校验。

        Args:
            operation：宿主固定的 write/edit，不来自子项参数。
            path：本项相对路径。
            arguments：通过对应 schema 的内容与版本。
        """
        from .write_admission import locked
        async with locked(self._lock):
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
