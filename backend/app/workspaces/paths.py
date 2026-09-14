"""W1a 工作区路径规范化、containment 与敏感路径规则。"""
from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath

_DRIVE = re.compile(r"^[A-Za-z]:")
_ENVIRONMENT = re.compile(r"\$\{|\$[A-Za-z_]|%[^%]+%")
_GLOB_CHARS = frozenset("*?[]{}")
_SENSITIVE_NAMES = frozenset({
    ".git", ".env", ".jwt-secret", ".api-key-secret", ".ssh", ".aws",
    "id_rsa", "id_ed25519", "credentials", "credentials.json",
})
_SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
MAX_RELATIVE_PATH_CHARS = 1024
MAX_PATH_DEPTH = 20


class WorkspacePathError(ValueError):
    """携带稳定错误码的路径拒绝。"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def normalize_relative_path(value: str, *, allow_root: bool = False) -> str:
    """把 API/工具路径规范为 POSIX 相对形式并拒绝跨平台危险语法。

    Args:
        value：调用方提供的相对路径。
        allow_root：是否允许 `.` 表示工作区根目录。
    """
    if not isinstance(value, str) or not value or len(value) > MAX_RELATIVE_PATH_CHARS:
        raise WorkspacePathError("WORKSPACE_PATH_INVALID")
    try:
        value.encode('utf-8')
    except UnicodeError:
        raise WorkspacePathError('WORKSPACE_PATH_INVALID') from None
    if "\x00" in value or "\\" in value or value.startswith(("/", "//", "~")) or _DRIVE.match(value):
        raise WorkspacePathError("WORKSPACE_PATH_INVALID")
    if _ENVIRONMENT.search(value) or any(char in value for char in _GLOB_CHARS):
        raise WorkspacePathError("WORKSPACE_PATH_INVALID")
    path = PurePosixPath(value)
    parts = path.parts
    if value == "." and allow_root:
        return "."
    if path.as_posix() != value:
        raise WorkspacePathError("WORKSPACE_PATH_INVALID")
    if not parts or any(part in {"", ".", ".."} for part in parts) or len(parts) > MAX_PATH_DEPTH:
        raise WorkspacePathError("WORKSPACE_PATH_INVALID")
    if any(len(part.encode("utf-8")) > 255 for part in parts):
        raise WorkspacePathError("WORKSPACE_PATH_INVALID")
    lowered = [part.casefold() for part in parts]
    if any(name in _SENSITIVE_NAMES or name.endswith(_SENSITIVE_SUFFIXES) for name in lowered):
        raise WorkspacePathError("WORKSPACE_PATH_SENSITIVE")
    return path.as_posix()


def resolve_workspace_root(value: str, *, require_exists: bool = True) -> Path:
    """把 Owner 输入的绝对目录规范化为 Workspace 根。

    Args:
        value：Owner 从前端提交或数据库读取的主机绝对路径。
        require_exists：是否要求目标目录已经存在；创建空目录前为 false。
    """
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 2048
        or "\x00" in value
        or value.startswith("~")
        or _ENVIRONMENT.search(value)
        or any(char in value for char in _GLOB_CHARS)
    ):
        raise WorkspacePathError("WORKSPACE_ROOT_PATH_INVALID")
    root = Path(value)
    if not root.is_absolute():
        raise WorkspacePathError("WORKSPACE_ROOT_PATH_INVALID")
    if is_link_like(root):
        raise WorkspacePathError("WORKSPACE_ROOT_PATH_INVALID")
    if not require_exists:
        try:
            parent = root.parent.resolve(strict=True)
        except (OSError, RuntimeError):
            raise WorkspacePathError("WORKSPACE_ROOT_NOT_AVAILABLE") from None
        if not parent.is_dir():
            raise WorkspacePathError("WORKSPACE_ROOT_NOT_AVAILABLE")
        return parent / root.name
    try:
        resolved = root.resolve(strict=True)
    except (OSError, RuntimeError):
        raise WorkspacePathError("WORKSPACE_ROOT_NOT_AVAILABLE") from None
    if not resolved.is_dir():
        raise WorkspacePathError("WORKSPACE_ROOT_NOT_AVAILABLE")
    return resolved


def _inside(root: Path, candidate: Path) -> bool:
    """判断 candidate 是否位于 root 内，避免字符串前缀误判。"""
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def is_link_like(path: Path) -> bool:
    """跨平台识别 symlink 与 Windows junction/reparse directory。"""
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(is_junction and is_junction())


def resolve_workspace_path(
    root: Path,
    relative_path: str,
    *,
    allow_root: bool = False,
    require_exists: bool = True,
    reject_final_symlink: bool = True,
) -> Path:
    """逐组件解析工作区路径并拒绝 symlink/junction 逃逸。

    Args:
        root：已经 canonicalize 的工作区根目录。
        relative_path：待解析的相对路径。
        allow_root：是否允许 `.`。
        require_exists：最终目标是否必须存在。
        reject_final_symlink：最终目标是链接时是否拒绝；list 子项自行报告链接。
    """
    normalized = normalize_relative_path(relative_path, allow_root=allow_root)
    if normalized == ".":
        return root
    current = root
    parts = PurePosixPath(normalized).parts
    for index, part in enumerate(parts):
        current = current / part
        is_final = index == len(parts) - 1
        if not current.exists() and not is_link_like(current):
            if is_final and not require_exists:
                parent = current.parent.resolve(strict=True)
                if not _inside(root, parent):
                    raise WorkspacePathError("WORKSPACE_PATH_OUTSIDE_ROOT")
                return current
            raise WorkspacePathError("WORKSPACE_FILE_NOT_FOUND")
        if is_link_like(current):
            if not is_final or reject_final_symlink:
                raise WorkspacePathError("WORKSPACE_PATH_OUTSIDE_ROOT")
        try:
            canonical = current.resolve(strict=True)
        except (OSError, RuntimeError):
            raise WorkspacePathError("WORKSPACE_PATH_OUTSIDE_ROOT") from None
        if not _inside(root, canonical):
            raise WorkspacePathError("WORKSPACE_PATH_OUTSIDE_ROOT")
        current = canonical
    return current
