"""监管器回收凭据：只含身份与一次性随机证明，不含脚本、环境或执行日志。"""
from dataclasses import dataclass
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets

import psutil

from ..config import settings
from ..workspaces.process_identity import same_birth


@dataclass(frozen=True)
class Receipt:
    """已经核对资源身份的安全回收结果，不向调用者传递原证明令牌。"""
    exit_code: int


def directory() -> Path:
    """凭据随 World 隔离；兼容 DB 模式使用连接配置的不可逆标识划分，路径中不含凭据。"""
    if settings.world_managed:
        return settings.world_dir / '.runtime-receipts'
    identity = hashlib.sha256(settings.database_url.encode()).hexdigest()[:24]
    return settings.storage_dir / 'runtime-receipts' / identity


def path_for(runtime_id: str) -> Path:
    """Args:
        runtime_id：后端随机生成的固定十六进制资源身份，不能是模型提供的文件路径。
    """
    if re.fullmatch('[a-f0-9]{32}', runtime_id) is None:
        raise ValueError('Invalid runtime receipt identity')
    return directory() / f'{runtime_id}.json'


def prepare(runtime_id: str) -> tuple[Path, bytes]:
    """建立私有目录及一次性证明令牌；数据库仅保存哈希，令牌通过独立管道交给监管器。

    Args:
        runtime_id：本次唯一启动实例。
    """
    path = path_for(runtime_id)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise OSError('Runtime receipt already exists')
    return path, secrets.token_bytes(32)


def read(row) -> Receipt | None:
    """有界读取并验证证明；缺失、损坏或身份不符均不推测为成功。

    Args:
        row：当前 World 数据库中可信的运行登记。
    """
    if not row.recovery_token_hash:
        return None
    try:
        path = path_for(row.id)
        if path.is_symlink():
            return None
        with path.open('rb') as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            return None
        body = json.loads(raw)
        token = bytes.fromhex(body['token'])
        if len(token) != 32 or not hmac.compare_digest(hashlib.sha256(token).hexdigest(), row.recovery_token_hash):
            return None
        if body['runtime_id'] != row.id or body['verified'] is not True or type(body['exit_code']) is not int:
            return None
        if row.pid is not None and body['pid'] != row.pid:
            return None
        if row.birth is not None and body['birth'] != row.birth:
            return None
        return Receipt(exit_code=body['exit_code'])
    except (OSError, ValueError, TypeError, KeyError):
        return None


def old_root_gone(row) -> bool:
    """确认原根进程已退出或 PID 已被复用；权限不足时保守返回否。

    Args:
        row：包含 PID 和出生身份的原运行登记。
    """
    if row.pid is None:
        return True
    try:
        process = psutil.Process(row.pid)
        return (row.birth is not None and not same_birth(row.pid, row.birth)) or process.status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True
    except psutil.AccessDenied:
        return False


def discard(row) -> bool:
    """终态提交后移除匹配凭据；失败保留原文件供后续核查，不删除未知证据。

    Args:
        row：已经持久化的终态运行登记。
    """
    try:
        path = path_for(row.id)
        if read(row) is not None:
            path.unlink()
        elif path.exists():
            return False
        try:
            path.parent.rmdir()
        except OSError:
            pass  # 其他服务还持有凭据时不能删除目录。
        return True
    except OSError:
        return False


def recorded_ids():
    """只枚举本数据库命名空间中的固定资源名，不扫描其他 World 或任意工作区。"""
    for path in directory().glob('*.json'):
        if re.fullmatch('[a-f0-9]{32}', path.stem):
            yield path.stem
