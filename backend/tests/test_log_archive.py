"""日志 v2 tar.gz 归档、保留、容量与删除审计测试。"""
from __future__ import annotations

import json
import os
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


class _Clock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def now(self) -> datetime:
        return self.current


def _write_jsonl(path: Path, event: str, payload: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"event": event, "payload": payload}) + "\n", encoding="utf-8")


def test_new_month_archives_previous_month_with_verified_manifest_and_audit(tmp_path: Path):
    """新月份启动时才分类归档上月日志，校验和审计后删除源目录。"""
    from app.config.log_archive import maintain_logs
    from app.config.logging import configure_logging, shutdown_logging

    now = datetime(2026, 9, 1, 8, 0, tzinfo=timezone(timedelta(hours=8)))
    _write_jsonl(tmp_path / "runtime/2026-08-25/app.jsonl", "old.runtime", "x" * 500)
    _write_jsonl(tmp_path / "tests/unit/2026-08-25/summary.jsonl", "old.unit")
    e2e_run = tmp_path / "tests/e2e/fake/2026-08-25/10-00-00_a8137c2f"
    _write_jsonl(e2e_run / "events.jsonl", "old.e2e")
    (e2e_run / "summary.json").write_text('{"status":"passed"}', encoding="utf-8")

    configure_logging(tmp_path, run_kind="runtime", world_name="default", clock=_Clock(now))
    result = maintain_logs(tmp_path, now=now, retention_days=30, max_total_bytes=1024 * 1024)
    shutdown_logging()

    assert result["archives_created"] == 3
    archive = tmp_path / "archive/2026-08/2026-08-25_runtime.tar.gz"
    assert archive.exists()
    assert not (tmp_path / "runtime/2026-08-25").exists()
    with tarfile.open(archive, "r:gz") as bundle:
        manifest = json.load(bundle.extractfile("manifest.json"))
    assert manifest["archive_kind"] == "runtime"
    assert manifest["files"][0]["path"] == "runtime/2026-08-25/app.jsonl"
    current_app = tmp_path / "runtime/2026-09-01/app.jsonl"
    records = [json.loads(line) for line in current_app.read_text().splitlines()]
    events = [record["event"] for record in records]
    assert events[0] == "log.retention_started"
    assert records[0]["archive_before"] == "2026-09-01"
    assert "log.archive_created" in events
    assert "log.archive_source_removed" in events
    assert events[-1] == "log.retention_completed"


def test_retention_and_size_delete_oldest_archive_with_planned_audit(tmp_path: Path):
    """30 天和容量均从最老正式归档开始，删除前后的审计事件可关联。"""
    from app.config.log_archive import maintain_logs
    from app.config.logging import configure_logging, shutdown_logging

    first_now = datetime(2026, 7, 1, 8, 0, tzinfo=timezone.utc)
    _write_jsonl(tmp_path / "runtime/2026-06-30/app.jsonl", "old", "x" * 2000)
    configure_logging(tmp_path, run_kind="runtime", world_name="default", clock=_Clock(first_now))
    maintain_logs(tmp_path, now=first_now, retention_days=30, max_total_bytes=10 * 1024 * 1024)
    shutdown_logging()

    later = datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc)
    configure_logging(tmp_path, run_kind="runtime", world_name="default", clock=_Clock(later))
    result = maintain_logs(tmp_path, now=later, retention_days=30, max_total_bytes=10 * 1024 * 1024)
    shutdown_logging()
    assert result["archives_deleted"] == 2
    assert not (tmp_path / "archive/2026-06/2026-06-30_runtime.tar.gz").exists()
    events = [json.loads(line) for line in (tmp_path / "runtime/2026-08-01/app.jsonl").read_text().splitlines()]
    planned = next(item for item in events if item["event"] == "log.archive_delete_planned")
    deleted = next(item for item in events if item["event"] == "log.archive_deleted")
    assert deleted["planned_event_id"] == planned["event_id"]


def test_archive_verification_failure_keeps_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """tar.gz 校验失败时原日志保持不动，不能出现先删后报错。"""
    from app.config import log_archive
    from app.config.logging import configure_logging, shutdown_logging

    now = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    source = tmp_path / "runtime/2026-08-25/app.jsonl"
    _write_jsonl(source, "must.remain")
    monkeypatch.setattr(log_archive, "verify_archive", lambda _path: (_ for _ in ()).throw(ValueError("corrupt")))
    configure_logging(tmp_path, run_kind="runtime", world_name="default", clock=_Clock(now))
    result = log_archive.maintain_logs(tmp_path, now=now)
    shutdown_logging()
    assert source.exists()
    assert result["failures"] == 1


def test_current_month_and_running_e2e_are_never_archived(tmp_path: Path):
    """当前月每日目录和 status=running 的历史 E2E 都属于受保护事实。"""
    from app.config.log_archive import maintain_logs
    from app.config.logging import configure_logging, shutdown_logging

    now = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
    current = tmp_path / "runtime/2026-09-02/app.jsonl"
    _write_jsonl(current, "active")
    previous_day = tmp_path / "runtime/2026-09-01/app.jsonl"
    _write_jsonl(previous_day, "still-readable")
    run = tmp_path / "tests/e2e/fake/2026-08-25/10-00-00_a8137c2f"
    run.mkdir(parents=True)
    (run / "summary.json").write_text('{"status":"running"}', encoding="utf-8")
    configure_logging(tmp_path, run_kind="runtime", world_name="default", clock=_Clock(now))
    maintain_logs(tmp_path, now=now)
    shutdown_logging()
    assert current.exists()
    assert previous_day.exists()
    assert run.exists()


def test_e2e_without_terminal_summary_is_never_archived(tmp_path: Path):
    """缺失或未知 status 不能被猜成已完成并删除。"""
    from app.config.log_archive import maintain_logs
    from app.config.logging import configure_logging, shutdown_logging

    now = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    run = tmp_path / "tests/e2e/fake/2026-08-25/10-00-00_a8137c2f"
    run.mkdir(parents=True)
    (run / "summary.json").write_text('{}', encoding="utf-8")
    configure_logging(tmp_path, run_kind="runtime", world_name="default", clock=_Clock(now))
    maintain_logs(tmp_path, now=now)
    shutdown_logging()

    assert run.exists()


def test_existing_archive_must_match_source_before_source_is_removed(tmp_path: Path):
    """同名正式归档与当前源内容不一致时，必须保留源目录。"""
    from app.config.log_archive import maintain_logs
    from app.config.logging import configure_logging, shutdown_logging

    now = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    source = tmp_path / "runtime/2026-08-25/app.jsonl"
    _write_jsonl(source, "original")
    configure_logging(tmp_path, run_kind="runtime", world_name="default", clock=_Clock(now))
    maintain_logs(tmp_path, now=now)
    shutdown_logging()

    _write_jsonl(source, "changed-after-archive")
    configure_logging(tmp_path, run_kind="runtime", world_name="default", clock=_Clock(now))
    result = maintain_logs(tmp_path, now=now)
    shutdown_logging()

    assert result["failures"] == 1
    assert source.exists()


def test_size_limit_prunes_oldest_recent_archive_first(tmp_path: Path):
    """未满 30 天但总量超限时，只按来源日期从旧到新淘汰正式归档。"""
    from app.config.log_archive import maintain_logs
    from app.config.logging import configure_logging, shutdown_logging

    now = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    for day in ("2026-08-24", "2026-08-25"):
        source = tmp_path / "runtime" / day / "payload.bin"
        source.parent.mkdir(parents=True)
        source.write_bytes(os.urandom(100_000))
    configure_logging(tmp_path, run_kind="runtime", world_name="default", clock=_Clock(now))
    result = maintain_logs(tmp_path, now=now, max_total_bytes=150_000)
    shutdown_logging()

    oldest = tmp_path / "archive/2026-08/2026-08-24_runtime.tar.gz"
    newest = tmp_path / "archive/2026-08/2026-08-25_runtime.tar.gz"
    assert result["archives_deleted"] == 1
    assert not oldest.exists()
    assert newest.exists()
    events = [json.loads(line) for line in (tmp_path / "runtime/2026-09-01/app.jsonl").read_text().splitlines()]
    planned = [item for item in events if item["event"] == "log.archive_delete_planned"]
    assert [item["reason"] for item in planned] == ["size"]
