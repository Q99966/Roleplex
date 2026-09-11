"""世界目录的创建、发现、一致性备份与旧数据接管。"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import psutil

from .compatibility import WorldRequiresNewerRoleplex
from ..workspaces.process_identity import birth_identity, same_birth


WORLD_FORMAT_VERSION = 1
_MANIFEST = "world.json"
_DATABASE = "roleplex.db"
_SECRET_FILES = (".jwt-secret", ".api-key-secret")
_ACTIVE_LEASE = ".active.json"
_WINDOWS_INVALID = frozenset('<>:"/\\|?*')


def validate_world_name(name: str) -> str:
    """校验可跨 Windows/WSL 使用的世界目录名。

    Args:
        name：用户输入或环境变量提供的世界名。

    Returns:
        去除首尾空白后的安全名称。

    Raises:
        ValueError：名称为空、过长、包含路径/控制字符或 Windows 非法字符。
    """
    normalized = name.strip()
    if not normalized or len(normalized) > 64:
        raise ValueError("世界名长度必须在 1 到 64 个字符之间")
    if normalized in {".", ".."} or any(character in _WINDOWS_INVALID for character in normalized):
        raise ValueError("世界名包含非法路径字符")
    if any(ord(character) < 32 for character in normalized):
        raise ValueError("世界名不能包含控制字符")
    return normalized


@dataclass(frozen=True)
class WorldInfo:
    """一个已通过元数据校验的世界目录。"""

    name: str
    path: Path
    created_at: str
    format_version: int

    @property
    def database_path(self) -> Path:
        return self.path / _DATABASE

    @property
    def files_path(self) -> Path:
        return self.path / "files"


class WorldActiveError(RuntimeError):
    """目标世界正由一个存活的后端进程使用。"""


class WorldManager:
    """只在受控根目录内管理物理隔离的世界。"""

    def __init__(self, root: str | Path) -> None:
        """绑定世界根目录，不在构造时创建具体世界。

        Args:
            root：全部世界目录的共同父目录。
        """
        self.root = Path(root)

    def world_path(self, name: str) -> Path:
        """返回经过校验且保证位于根目录下的世界路径。"""
        return self.root / validate_world_name(name)

    def _write_manifest(self, directory: Path, name: str, created_at: str | None = None) -> None:
        payload = {
            "name": name,
            "format_version": WORLD_FORMAT_VERSION,
            "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        }
        temporary = directory / f".{_MANIFEST}.tmp-{os.getpid()}"
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(directory / _MANIFEST)

    @staticmethod
    def _create_secret(path: Path) -> None:
        """原子创建世界密钥，已存在时绝不覆盖。"""
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(secrets.token_urlsafe(48))

    def create(self, name: str) -> WorldInfo:
        """创建空世界及两份独立密钥。

        Raises:
            FileExistsError：同名目录已经存在，避免覆盖未知数据。
        """
        normalized = validate_world_name(name)
        directory = self.world_path(normalized)
        directory.mkdir(parents=True, exist_ok=False)
        try:
            (directory / "files").mkdir()
            sqlite3.connect(directory / _DATABASE).close()
            for filename in _SECRET_FILES:
                self._create_secret(directory / filename)
            self._write_manifest(directory, normalized)
            return self.get(normalized)
        except Exception:
            # 只回收本函数刚创建且尚未对外可见的半成品目录。
            shutil.rmtree(directory, ignore_errors=True)
            raise

    def get(self, name: str) -> WorldInfo:
        """读取并校验世界元数据。"""
        normalized = validate_world_name(name)
        directory = self.world_path(normalized)
        manifest_path = directory / _MANIFEST
        if not manifest_path.is_file():
            raise FileNotFoundError(normalized)
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if payload.get("name") != normalized or not isinstance(payload.get("format_version"), int):
            raise ValueError(f"世界元数据无效：{normalized}")
        if payload["format_version"] > WORLD_FORMAT_VERSION:
            raise WorldRequiresNewerRoleplex(
                "这个世界来自更新版本的 Roleplex，请升级软件后再打开；世界数据没有被修改。"
            )
        return WorldInfo(
            name=normalized,
            path=directory,
            created_at=str(payload.get("created_at", "")),
            format_version=payload["format_version"],
        )

    def ensure(self, name: str) -> WorldInfo:
        """返回已有世界；目录不存在时创建，并可补齐安全的创建半成品。"""
        try:
            return self.get(name)
        except FileNotFoundError:
            normalized = validate_world_name(name)
            directory = self.world_path(normalized)
            if not directory.exists():
                return self.create(normalized)
            allowed = {_DATABASE, "files", *_SECRET_FILES}
            unknown = {entry.name for entry in directory.iterdir()} - allowed
            if unknown:
                raise ValueError(f"世界目录缺少元数据且包含未知文件：{sorted(unknown)}")
            (directory / "files").mkdir(exist_ok=True)
            sqlite3.connect(directory / _DATABASE).close()
            for filename in _SECRET_FILES:
                self._create_secret(directory / filename)
            self._write_manifest(directory, normalized)
            return self.get(normalized)

    def list_worlds(self) -> list[WorldInfo]:
        """只列出带合法元数据的世界，忽略其他文件和半成品目录。"""
        if not self.root.is_dir():
            return []
        worlds: list[WorldInfo] = []
        for directory in self.root.iterdir():
            if not directory.is_dir():
                continue
            try:
                worlds.append(self.get(directory.name))
            except (ValueError, FileNotFoundError, WorldRequiresNewerRoleplex, json.JSONDecodeError, OSError):
                continue
        return sorted(worlds, key=lambda world: world.name.casefold())

    @staticmethod
    def _snapshot_database(source: Path, destination: Path) -> None:
        """使用 SQLite backup API 获取包含 WAL 内容的一致性快照。"""
        destination.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(source) as source_connection, sqlite3.connect(destination) as target_connection:
            source_connection.backup(target_connection)

    def backup(self, name: str, destination: str | Path) -> Path:
        """把一致性数据库快照、密钥、元数据和附件打成 ZIP。"""
        world = self.get(name)
        self.acquire(name)
        try:
            if self._has_unfinished_runtime(world.database_path):
                raise WorldActiveError('备份前请正常关闭该世界后端并确认进程已回收；不能在服务可能写入时备份。')
            return self._archive_world(name, destination)
        finally:
            self.release(name)

    def backup_owned(self, name: str, destination: str | Path) -> Path:
        """当前后端在持有 World 租约及回收门槛期间导出，不授权其他进程绕过租约。

        Args:
            name：当前后端经认证的 World。
            destination：宿主创建的独占临时导出目录。
        """
        world = self.get(name)
        try:
            lease = json.loads(self._lease_path(name).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise WorldActiveError('当前进程未持有世界租约') from None
        if lease.get('pid') != os.getpid() or not same_birth(os.getpid(), lease.get('birth')) or self._has_unfinished_runtime(world.database_path):
            raise WorldActiveError('世界租约不属于当前后端或仍有未回收实例')
        return self._archive_world(name, destination)

    def _archive_world(self, name: str, destination: str | Path) -> Path:
        """生成归档；调用者负责持有租约/运行门槛并验证归属。

        Args:
            name：已验证世界。
            destination：目标目录，不来自未授权模型输入。
        """
        world = self.get(name)
        output_dir = Path(destination)
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().astimezone().strftime("%Y%m%d%H%M%S")
        archive_base = output_dir / f"{world.name}-backup-{timestamp}"
        with tempfile.TemporaryDirectory(prefix="roleplex-world-backup-") as temporary:
            staged = Path(temporary) / world.name
            staged.mkdir()
            self._snapshot_database(world.database_path, staged / _DATABASE)
            shutil.copy2(world.path / _MANIFEST, staged / _MANIFEST)
            for filename in _SECRET_FILES:
                shutil.copy2(world.path / filename, staged / filename)
            if world.files_path.is_dir():
                shutil.copytree(world.files_path, staged / "files")
            else:
                (staged / "files").mkdir()
            archive = shutil.make_archive(str(archive_base), "zip", root_dir=temporary, base_dir=world.name)
        return Path(archive)

    def adopt_legacy(self, name: str, legacy_data_dir: str | Path) -> WorldInfo:
        """一致性复制旧开发库及密钥，保留原目录作为回退。"""
        legacy = Path(legacy_data_dir)
        source_database = legacy / _DATABASE
        if not source_database.is_file():
            raise FileNotFoundError(source_database)
        target = self.world_path(name)
        target_preexisting = target.exists()
        try:
            self.get(name)
        except FileNotFoundError:
            world = self.ensure(name) if target.exists() else self.create(name)
        else:
            raise FileExistsError(f"目标世界已经存在：{name}")
        try:
            self._snapshot_database(source_database, world.database_path)
            for filename in _SECRET_FILES:
                source = legacy / filename
                if source.is_file():
                    shutil.copy2(source, world.path / filename)
            source_files = legacy / "files"
            if source_files.is_dir():
                shutil.copytree(source_files, world.files_path, dirs_exist_ok=True)
            return self.get(name)
        except Exception:
            if not target_preexisting:
                shutil.rmtree(world.path, ignore_errors=True)
            raise

    def delete(self, name: str) -> None:
        """删除一个已验证的世界目录；调用方必须先完成显式确认。"""
        world = self.get(name)
        self.acquire(name)
        try:
            if self._has_unfinished_runtime(world.database_path):
                raise WorldActiveError(f"世界仍有未确认回收的实例，不能删除：{world.name}")
            # 先原子移出可启动名称，再清理文件；不能在逐文件删除租约后允许新后端打开半个世界。
            retired = world.path.with_name(f'.deleted-{secrets.token_hex(16)}')
            world.path.rename(retired)
        except BaseException:
            self.release(name)
            raise
        shutil.rmtree(retired)

    @staticmethod
    def _has_unfinished_runtime(database: Path) -> bool:
        """在世界文件适配层检查未确认运行记录，不按 PID 猜测已回收。

        Args:
            database：经过世界归属验证的数据库文件。
        """
        if not database.is_file():
            return False
        from sqlalchemy import MetaData, Table, create_engine, inspect, select
        engine = create_engine(f'sqlite:///{database}', hide_parameters=True)
        try:
            if not inspect(engine).has_table('runtime_entries'):
                return False
            table = Table('runtime_entries', MetaData(), autoload_with=engine, include_columns=['state'])
            with engine.connect() as connection:
                return connection.scalar(select(table.c.state).where(table.c.state.in_(
                    ('pending', 'starting', 'waiting_ready', 'ready', 'unhealthy', 'running', 'stopping', 'cleanup_required'))).limit(1)) is not None
        finally:
            engine.dispose()

    def _lease_path(self, name: str) -> Path:
        return self.world_path(name) / _ACTIVE_LEASE

    def is_active(self, name: str) -> bool:
        """只读检查租约；损坏租约保守阻止操作，过期租约只由持锁的 acquire 清理。"""
        lease = self._lease_path(name)
        if not lease.is_file():
            return False
        try:
            payload = json.loads(lease.read_text(encoding="utf-8"))
            pid = int(payload["pid"])
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            return True
        if pid <= 0:
            return True
        if psutil.pid_exists(pid):
            return True
        return False

    @contextmanager
    def _lease_lock(self, name: str):
        """用 OS 文件锁保护租约的检查/回收/创建，不把它用作业务数据库写锁。

        Args:
            name：已验证的世界名；锁文件位于世界目录之外，删除/换名不会丢锁。
        """
        normalized = validate_world_name(name)
        directory = self.root / '.lease-locks'
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / f'{normalized}.lock').open('a+b') as stream:
            if os.name == 'nt':
                import msvcrt
                if stream.tell() == 0:
                    stream.write(b'\0')
                    stream.flush()
                stream.seek(0)
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    raise WorldActiveError('世界租约正在被其他操作修改') from None
                try:
                    yield
                finally:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise WorldActiveError('世界租约正在被其他操作修改') from None
                try:
                    yield
                finally:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def acquire(self, name: str) -> None:
        """为当前后端进程原子取得世界活动租约。"""
        with self._lease_lock(name):
            self._acquire_locked(name)

    def _acquire_locked(self, name: str) -> None:
        """Args:
            name：调用者持有该世界租约文件锁。
        """
        world = self.get(name)
        lease = self._lease_path(name)
        if self.is_active(name):
            raise WorldActiveError(f"世界已被其他进程使用：{world.name}")
        lease.unlink(missing_ok=True)
        payload = json.dumps({"pid": os.getpid(), "birth": birth_identity(os.getpid()),
            "started_at": datetime.now(timezone.utc).isoformat()})
        try:
            descriptor = os.open(lease, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise WorldActiveError(f"世界租约竞争失败：{world.name}") from exc
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)

    def release(self, name: str) -> None:
        """仅释放属于当前进程的活动租约。"""
        with self._lease_lock(name):
            self._release_locked(name)

    def _release_locked(self, name: str) -> None:
        """Args:
            name：调用者持有该世界租约文件锁。
        """
        lease = self._lease_path(name)
        try:
            payload = json.loads(lease.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return
        if payload.get("pid") == os.getpid() and same_birth(os.getpid(), payload.get('birth')):
            lease.unlink(missing_ok=True)

    def request_switch(self, name: str, control_file: str | Path) -> None:
        """原子写入包装器控制文件，请求下次启动选择目标世界。"""
        world = self.get(name)
        target = Path(control_file)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.tmp-{os.getpid()}")
        temporary.write_text(world.name, encoding="utf-8")
        temporary.replace(target)
