"""日志 v2 启动归档：tar.gz 校验、30 天保留、1 GiB 上限与删除审计。"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import shutil
import tarfile
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Iterator
from uuid import uuid4

import psutil

from .logging import BEIJING_TZ, ensure_log_directory, flush_persistent_logs, restrict_log_file


logger = logging.getLogger("roleplex.log_archive")
ARCHIVE_KINDS = ("runtime", "unit-tests", "e2e-fake", "e2e-real")
_ARCHIVE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(runtime|unit-tests|e2e-fake|e2e-real)\.tar\.gz$")


@dataclass(frozen=True)
class ArchiveCandidate:
    source_date: date
    kind: str
    source: Path


def _sha256(path: Path) -> str:
    """流式计算文件 SHA-256。

    Args:
        path：待读取文件。
    """
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_bytes(root: Path) -> int:
    """统计日志树当前实际文件字节数。

    Args:
        root：日志树根目录。
    """
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file()) if root.exists() else 0


def _safe_member_name(name: str) -> bool:
    """拒绝绝对路径和包含父目录跳转的 tar 成员名。

    Args:
        name：tar 中使用 POSIX 分隔符的成员名。
    """
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts and bool(path.parts)


def verify_archive(path: Path) -> dict:
    """完整读取 tar.gz 并按 manifest 校验成员类型、路径、大小和哈希。

    Args:
        path：待校验的正式或临时归档。

    Raises:
        ValueError：成员类型、路径或 manifest 内容不安全/不一致。
    """
    with tarfile.open(path, "r:gz") as bundle:
        members = bundle.getmembers()
        for member in members:
            if not _safe_member_name(member.name) or member.issym() or member.islnk() or member.isdev():
                raise ValueError(f"归档包含不安全成员：{member.name}")
            if not (member.isfile() or member.isdir()):
                raise ValueError(f"归档包含未知成员类型：{member.name}")
        manifest_member = bundle.getmember("manifest.json")
        manifest_stream = bundle.extractfile(manifest_member)
        if manifest_stream is None:
            raise ValueError("归档缺少 manifest.json")
        manifest = json.load(manifest_stream)
        expected = {item["path"]: item for item in manifest.get("files", [])}
        actual = {member.name for member in members if member.isfile() and member.name != "manifest.json"}
        if actual != set(expected):
            raise ValueError("归档成员与 manifest 不一致")
        for name, item in expected.items():
            member = bundle.getmember(name)
            stream = bundle.extractfile(member)
            if stream is None:
                raise ValueError(f"无法读取归档成员：{name}")
            data = stream.read()
            if len(data) != item["size"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise ValueError(f"归档成员校验失败：{name}")
        return manifest


def _create_archive(candidate: ArchiveCandidate, destination: Path, logs_root: Path, compresslevel: int) -> dict:
    """创建并完整校验一个不可变 tar.gz。

    Args:
        candidate：待归档的日期/类别源目录。
        destination：正式归档目标路径。
        logs_root：计算成员相对路径所用的日志根目录。
        compresslevel：gzip 压缩等级。
    """
    files = sorted(path for path in candidate.source.rglob("*") if path.is_file() and not path.is_symlink())
    manifest = {
        "schema_version": 2,
        "source_date": candidate.source_date.isoformat(),
        "archive_kind": candidate.kind,
        "created_at": datetime.now(BEIJING_TZ).isoformat(),
        "files": [
            {
                "path": path.relative_to(logs_root).as_posix(),
                "size": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in files
        ],
    }
    ensure_log_directory(destination.parent)
    temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    manifest_data = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    try:
        with tarfile.open(temporary, "w:gz", compresslevel=compresslevel) as bundle:
            info = tarfile.TarInfo("manifest.json")
            info.size = len(manifest_data)
            info.mtime = int(time.time())
            info.mode = 0o640
            bundle.addfile(info, io.BytesIO(manifest_data))
            for path, item in zip(files, manifest["files"], strict=True):
                bundle.add(path, arcname=item["path"], recursive=False)
        restrict_log_file(temporary)
        verify_archive(temporary)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        temporary.replace(destination)
        restrict_log_file(destination)
        return manifest
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _source_file_manifest(candidate: ArchiveCandidate, logs_root: Path) -> list[dict[str, str | int]]:
    """从当前源目录生成确定性文件清单。

    Args:
        candidate：待归档源目录。
        logs_root：计算成员相对路径所用的日志根目录。
    """
    files = sorted(path for path in candidate.source.rglob("*") if path.is_file() and not path.is_symlink())
    return [
        {
            "path": path.relative_to(logs_root).as_posix(),
            "size": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in files
    ]


def _archive_matches_source(manifest: dict, candidate: ArchiveCandidate, logs_root: Path) -> bool:
    """正式归档重入时必须仍对应当前源，不能只因文件名相同就删除源。

    Args:
        manifest：已通过 tar 安全校验的归档清单。
        candidate：本轮待收尾的源日志目录。
        logs_root：计算清单相对路径所用的日志根目录。
    """
    return (
        manifest.get("schema_version") == 2
        and manifest.get("source_date") == candidate.source_date.isoformat()
        and manifest.get("archive_kind") == candidate.kind
        and manifest.get("files") == _source_file_manifest(candidate, logs_root)
    )


def _parse_date_dir(path: Path) -> date | None:
    """把目录名解析为本地自然日，非日期名称安全忽略。

    Args:
        path：候选日期目录。
    """
    try:
        return date.fromisoformat(path.name)
    except ValueError:
        return None


def _e2e_day_finished(path: Path) -> bool:
    """确认某日全部 E2E run 都有明确终态。

    Args:
        path：fake 或 real 下的日期目录。
    """
    run_dirs = [item for item in path.iterdir() if item.is_dir()]
    if not run_dirs:
        return False
    for run in run_dirs:
        summary = run / "summary.json"
        try:
            if json.loads(summary.read_text(encoding="utf-8")).get("status") not in {
                "passed", "failed", "interrupted",
            }:
                return False
        except (OSError, json.JSONDecodeError):
            return False
    return True


def _candidates(root: Path, archive_before: date) -> list[ArchiveCandidate]:
    """收集早于当前自然月且允许归档的 v2 来源目录。

    Args:
        root：日志树根目录。
        archive_before：当前月第一天；只有更早来源可以归档。
    """
    layouts = [
        (root / "runtime", "runtime", lambda _path: True),
        (root / "tests/unit", "unit-tests", lambda _path: True),
        (root / "tests/e2e/fake", "e2e-fake", _e2e_day_finished),
        (root / "tests/e2e/real", "e2e-real", _e2e_day_finished),
    ]
    result: list[ArchiveCandidate] = []
    for parent, kind, completed in layouts:
        if not parent.is_dir():
            continue
        for item in parent.iterdir():
            source_date = _parse_date_dir(item)
            if item.is_dir() and source_date is not None and source_date < archive_before and completed(item):
                result.append(ArchiveCandidate(source_date, kind, item))
    return sorted(result, key=lambda item: (item.source_date, item.kind))


def _archive_path(root: Path, source_date: date, kind: str) -> Path:
    """生成日期/类别对应的正式归档路径。

    Args:
        root：日志树根目录。
        source_date：归档内容的来源日期。
        kind：已登记归档类别。
    """
    return root / "archive" / source_date.strftime("%Y-%m") / f"{source_date.isoformat()}_{kind}.tar.gz"


def _audit(event: str, *, level: int = logging.INFO, event_id: str | None = None, **fields) -> str:
    """写入并强制 flush 一条删除安全所依赖的审计事实。

    Args:
        event：稳定审计事件名。
        level：日志级别。
        event_id：可选预分配事件身份。
        **fields：不含敏感原文的审计字段。
    """
    identity = event_id or f"evt_{uuid4().hex}"
    logger.log(level, event, extra={"event_id": identity, **fields})
    if not flush_persistent_logs():
        raise OSError("结构化审计日志无法 flush")
    return identity


def _remove_source(candidate: ArchiveCandidate, root: Path) -> tuple[int, int]:
    """验证路径边界后删除已核验归档的源目录。

    Args:
        candidate：已成功归档的源目录。
        root：用于防目录逃逸的日志根目录。
    """
    resolved = candidate.source.resolve()
    if root.resolve() not in resolved.parents:
        raise ValueError("归档源目录逃逸日志根目录")
    files = [path for path in candidate.source.rglob("*") if path.is_file()]
    count = len(files)
    size = sum(path.stat().st_size for path in files)
    shutil.rmtree(candidate.source)
    return count, size


def _formal_archives(root: Path) -> list[tuple[date, Path]]:
    """按来源日期返回允许参与保留策略的正式归档。

    Args:
        root：日志树根目录。
    """
    result = []
    archive_root = root / "archive"
    if not archive_root.is_dir():
        return result
    for path in archive_root.rglob("*.tar.gz"):
        match = _ARCHIVE_RE.match(path.name)
        if match:
            result.append((date.fromisoformat(match.group(1)), path))
    return sorted(result, key=lambda item: (item[0], item[1].name))


def _delete_archive(path: Path, source_date: date, reason: str, root: Path) -> int:
    """在 planned 审计落盘后删除一个已验证正式归档。

    Args:
        path：正式归档路径。
        source_date：归档内容来源日期。
        reason：age 或 size 淘汰原因。
        root：用于防路径逃逸的日志根目录。
    """
    resolved = path.resolve()
    if root.resolve() not in resolved.parents:
        raise ValueError("归档路径逃逸日志根目录")
    verify_archive(path)
    size = path.stat().st_size
    digest = _sha256(path)
    planned = _audit(
        "log.archive_delete_planned",
        archive_path=path.relative_to(root).as_posix(),
        archive_bytes=size,
        reason=reason,
        source_date=source_date.isoformat(),
        archive_sha256=digest,
    )
    path.unlink()
    _audit("log.archive_deleted", planned_event_id=planned, released_bytes=size)
    return size


@contextmanager
def _retention_lease(root: Path) -> Iterator[bool]:
    """获取跨进程清理租约；活跃持有者存在时返回 false。

    Args:
        root：日志树根目录。
    """
    archive_root = root / "archive"
    ensure_log_directory(archive_root)
    lease = archive_root / ".retention.lock"
    if lease.exists():
        try:
            pid = int(json.loads(lease.read_text(encoding="utf-8"))["pid"])
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            pid = -1
        if pid > 0 and psutil.pid_exists(pid):
            yield False
            return
        lease.unlink(missing_ok=True)
    try:
        descriptor = os.open(lease, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        yield False
        return
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump({"pid": os.getpid(), "started_at": datetime.now(BEIJING_TZ).isoformat()}, stream)
    try:
        yield True
    finally:
        lease.unlink(missing_ok=True)


def maintain_logs(
    logs_root: str | Path,
    *,
    now: datetime | None = None,
    retention_days: int = 30,
    max_total_bytes: int = 1_073_741_824,
    compresslevel: int = 6,
) -> dict[str, int]:
    """执行一次启动日志维护，失败按项记录且不阻断调用方。

    Args:
        logs_root：日志树根目录。
        now：可选本地当前时间，测试可注入。
        retention_days：按来源自然日计算的最长保留天数。
        max_total_bytes：整个日志树的目标容量上限。
        compresslevel：tar.gz 使用的 gzip 压缩等级。
    """
    root = Path(logs_root)
    current = (now or datetime.now(BEIJING_TZ)).astimezone(BEIJING_TZ)
    archive_before = current.date().replace(day=1)
    result = {"archives_created": 0, "archives_deleted": 0, "released_bytes": 0, "failures": 0}
    started = time.monotonic()
    bytes_before = _tree_bytes(root)
    with _retention_lease(root) as acquired:
        if not acquired:
            _audit("log.retention_skipped", reason="lease_busy")
            return result
        _audit(
            "log.retention_started",
            archive_before=archive_before.isoformat(),
            cutoff_date=(current.date() - timedelta(days=retention_days)).isoformat(),
            retention_days=retention_days,
            max_total_bytes=max_total_bytes,
            bytes_before=bytes_before,
        )
        for candidate in _candidates(root, archive_before):
            destination = _archive_path(root, candidate.source_date, candidate.kind)
            try:
                if destination.exists():
                    manifest = verify_archive(destination)
                    if not _archive_matches_source(manifest, candidate, root):
                        raise ValueError("现有正式归档与当前源日志不一致")
                else:
                    manifest = _create_archive(candidate, destination, root, compresslevel)
                    result["archives_created"] += 1
                archive_size = destination.stat().st_size
                _audit(
                    "log.archive_created",
                    source_date=candidate.source_date.isoformat(),
                    archive_kind=candidate.kind,
                    archive_path=destination.relative_to(root).as_posix(),
                    source_files=len(manifest["files"]),
                    source_bytes=sum(item["size"] for item in manifest["files"]),
                    archive_bytes=archive_size,
                    archive_sha256=_sha256(destination),
                )
                removed_count, removed_bytes = _remove_source(candidate, root)
                _audit(
                    "log.archive_source_removed",
                    source_date=candidate.source_date.isoformat(),
                    archive_kind=candidate.kind,
                    removed_files=removed_count,
                    removed_bytes=removed_bytes,
                )
            except Exception as exc:
                result["failures"] += 1
                logger.exception(
                    "log.retention_failed",
                    extra={
                        "stage": "archive",
                        "source_date": candidate.source_date.isoformat(),
                        "archive_kind": candidate.kind,
                        "error_type": type(exc).__name__,
                    },
                )

        for source_date, path in list(_formal_archives(root)):
            if (current.date() - source_date).days < retention_days:
                continue
            try:
                result["released_bytes"] += _delete_archive(path, source_date, "age", root)
                result["archives_deleted"] += 1
            except Exception as exc:
                result["failures"] += 1
                logger.exception("log.retention_failed", extra={"stage": "age_delete", "error_type": type(exc).__name__})

        total = _tree_bytes(root)
        for source_date, path in list(_formal_archives(root)):
            if total <= max_total_bytes:
                break
            try:
                released = _delete_archive(path, source_date, "size", root)
                total -= released
                result["released_bytes"] += released
                result["archives_deleted"] += 1
            except Exception as exc:
                result["failures"] += 1
                logger.exception("log.retention_failed", extra={"stage": "size_delete", "error_type": type(exc).__name__})

        bytes_after = _tree_bytes(root)
        if bytes_after > max_total_bytes and not _formal_archives(root):
            logger.critical(
                "log.retention_failed",
                extra={"stage": "capacity", "bytes_after": bytes_after, "max_total_bytes": max_total_bytes},
            )
        _audit(
            "log.retention_completed",
            bytes_before=bytes_before,
            bytes_after=bytes_after,
            duration_ms=round((time.monotonic() - started) * 1000, 2),
            **result,
        )
    return result
