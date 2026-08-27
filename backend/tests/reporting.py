"""pytest 日志 v2：整轮 summary 与仅失败测试的有界诊断文件。"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
LOG_ROOT = REPOSITORY_ROOT / "logs" / "tests" / "unit"
_SAFE_CASE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(password|passwd|token|authorization|api[_-]?key|cookie)(\s*[=:]\s*)([^\s,;]+)"
)
_REQUEST_ID = re.compile(r"request[_-]?id[=:]\s*([0-9a-f]{32})", re.IGNORECASE)
_started_monotonic = 0.0
_started_at: datetime | None = None
_failed_reports: dict[str, list[dict[str, Any]]] = defaultdict(list)
_safe_case_ids: dict[str, list[str]] = {}


def _redact_text(value: str) -> str:
    """过滤诊断文本中的敏感赋值和当前环境已知凭据。

    Args:
        value：pytest 捕获或异常产生的原始文本。
    """
    text = _SENSITIVE_ASSIGNMENT.sub(lambda match: f"{match.group(1)}{match.group(2)}<redacted>", value)
    for key, secret in os.environ.items():
        if len(secret) >= 4 and any(part in key.lower() for part in ("password", "token", "secret", "key")):
            text = text.replace(secret, "<redacted>")
    return text


def _tail_utf8(value: str, max_bytes: int) -> tuple[str, int, int, bool]:
    """按 UTF-8 字节保留脱敏文本尾部且不截断续字节。

    Args:
        value：待保存诊断文本。
        max_bytes：允许持久化的最大字节数。
    """
    encoded = _redact_text(value).encode("utf-8", errors="replace")
    original = len(encoded)
    if original <= max_bytes:
        return encoded.decode("utf-8"), original, original, False
    tail = encoded[-max_bytes:]
    while tail and (tail[0] & 0xC0) == 0x80:
        tail = tail[1:]
    decoded = tail.decode("utf-8", errors="replace")
    return decoded, original, len(tail), True


def bounded_capture(value: str, max_bytes: int, kind: str) -> dict[str, Any]:
    """返回保留尾部的 captured 字段与字节统计。

    Args:
        value：pytest 捕获的原始文本。
        max_bytes：脱敏后允许保存的最大 UTF-8 字节数。
        kind：stdout、stderr 或 logs 字段类别。
    """
    text, original, stored, truncated = _tail_utf8(value or "", max_bytes)
    key = "captured_logs" if kind == "logs" else f"captured_{kind}"
    return {
        key: text,
        f"{kind}_original_bytes": original,
        f"{kind}_stored_bytes": stored,
        f"{kind}_truncated": truncated,
    }


def failure_identity(nodeid: str, safe_case_ids: list[str] | None) -> dict[str, Any]:
    """从 raw nodeid 派生不含原始参数的持久化身份。

    Args:
        nodeid：只在内存中使用的 pytest 完整节点标识。
        safe_case_ids：测试显式登记、允许持久化的安全 case ID。
    """
    parts = nodeid.split("::")
    test_file = parts[0].replace("\\", "/")
    raw_name = parts[-1].split("[", 1)[0]
    test_name = raw_name[5:] if raw_name.startswith("test_") else raw_name
    identity: dict[str, Any] = {
        "test_file": test_file,
        "test_name": re.sub(r"[^A-Za-z0-9_-]+", "_", test_name).strip("_") or "unnamed",
        "nodeid_hash": hashlib.sha256(nodeid.encode("utf-8")).hexdigest()[:8],
    }
    safe = [value for value in (safe_case_ids or []) if _SAFE_CASE.fullmatch(value)]
    if safe:
        identity["parameter_ids"] = safe
    return identity


def classify_test_scope(arguments: list[str]) -> dict[str, Any]:
    """判断 pytest 是否完整运行默认普通测试集，并只保存安全选择描述。

    Args:
        arguments：pytest 本轮原始调用参数；敏感表达式只参与哈希。
    """
    selection_options = {"-k", "-m", "--lf", "--last-failed", "--ff", "--failed-first", "--stepwise"}
    partial = any(
        argument in selection_options
        or (argument.startswith("-k") and argument != "-k")
        or (argument.startswith("-m") and argument != "-m")
        or argument.startswith(("--ignore", "--deselect", "--stepwise", "--sw", "--new-first", "--nf"))
        for argument in arguments
    )
    paths: list[str] = []
    skip_next = False
    for index, argument in enumerate(arguments):
        if skip_next:
            skip_next = False
            continue
        if argument in {"-k", "-m"}:
            skip_next = True
            continue
        if argument.startswith("-"):
            continue
        normalized = argument.replace("\\", "/")
        if normalized not in {"tests", "./tests"}:
            partial = True
            safe_path = normalized.split("::", 1)[0]
            if re.fullmatch(r"[A-Za-z0-9_./-]+", safe_path):
                paths.append(safe_path)
    result: dict[str, Any] = {"test_scope": "partial" if partial else "full"}
    if partial:
        result["selection"] = sorted(set(paths))
        result["selection_hash"] = hashlib.sha256("\0".join(arguments).encode("utf-8")).hexdigest()[:8]
    return result


def database_display_path(database_url: str) -> str | None:
    """把 pytest SQLite 地址压缩为仓库相对路径，外部位置只保留文件名。

    Args:
        database_url：本轮 pytest 使用的数据库连接地址。
    """
    prefix = "sqlite+aiosqlite:///"
    if not database_url.startswith(prefix):
        return None
    candidate = Path(database_url.removeprefix(prefix))
    if not candidate.is_absolute():
        candidate = (BACKEND_ROOT / candidate).resolve()
    try:
        return candidate.relative_to(REPOSITORY_ROOT.resolve()).as_posix()
    except ValueError:
        return candidate.name


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    """在同目录临时写入后原子替换 JSON。

    Args:
        path：最终 JSON 路径。
        payload：只含安全字段的可序列化对象。
    """
    from app.config.logging import ensure_log_directory, restrict_log_file

    ensure_log_directory(path.parent)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    restrict_log_file(temporary)
    temporary.replace(path)
    restrict_log_file(path)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    """以单次 append 追加一条完整 summary JSON 行。

    Args:
        path：按日 summary 文件。
        payload：本轮测试汇总。
    """
    from app.config.logging import ensure_log_directory

    ensure_log_directory(path.parent)
    data = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    descriptor = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o640)
    try:
        os.write(descriptor, data)
    finally:
        os.close(descriptor)


def pytest_configure(config) -> None:
    """初始化本轮 reporter 状态并登记安全 case marker。

    Args:
        config：pytest 当前运行配置对象。
    """
    global _started_at, _started_monotonic
    _started_at = datetime.now().astimezone()
    _started_monotonic = time.monotonic()
    _failed_reports.clear()
    _safe_case_ids.clear()
    config.addinivalue_line("markers", "log_case(*ids): 允许写入失败报告的安全参数组标识")


def pytest_collection_modifyitems(items) -> None:
    """收集测试显式声明的安全日志 case ID。

    Args:
        items：pytest 已收集测试项。
    """
    for item in items:
        marker = item.get_closest_marker("log_case")
        if marker:
            _safe_case_ids[item.nodeid] = [str(value) for value in marker.args if _SAFE_CASE.fullmatch(str(value))]


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """在 pytest 丢弃结构化 excinfo 前把异常类名附加到阶段报告。

    Args:
        item：当前 pytest 测试项；钩子签名要求保留，本函数不读取其参数值。
        call：包含本阶段结构化异常信息的 CallInfo。
    """
    outcome = yield
    report = outcome.get_result()
    excinfo = getattr(call, "excinfo", None)
    exception_type = getattr(excinfo, "typename", None)
    report.roleplex_exception_type = (
        exception_type
        if isinstance(exception_type, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,127}", exception_type)
        else None
    )


def pytest_runtest_logreport(report) -> None:
    """聚合同一测试项各阶段的失败诊断。

    Args:
        report：pytest 当前阶段执行报告。
    """
    if not report.failed:
        return
    raw_traceback = getattr(report, "longreprtext", "") or str(report.longrepr)
    identity = failure_identity(report.nodeid, _safe_case_ids.get(report.nodeid))
    safe_label = f"{identity['test_file']}::{identity['test_name']}[{identity['nodeid_hash']}]"
    raw_traceback = raw_traceback.replace(report.nodeid, safe_label)
    traceback, traceback_original, traceback_stored, traceback_truncated = _tail_utf8(raw_traceback, 512 * 1024)
    crash = getattr(getattr(report, "longrepr", None), "reprcrash", None)
    message = _redact_text(str(getattr(crash, "message", "test phase failed")))
    phase = {
        "phase": report.when,
        "exception_type": getattr(report, "roleplex_exception_type", None),
        "message": message,
        "traceback": traceback,
        "traceback_original_bytes": traceback_original,
        "traceback_stored_bytes": traceback_stored,
        "traceback_truncated": traceback_truncated,
        **bounded_capture(getattr(report, "capstdout", ""), 64 * 1024, "stdout"),
        **bounded_capture(getattr(report, "capstderr", ""), 64 * 1024, "stderr"),
        **bounded_capture(getattr(report, "caplog", ""), 128 * 1024, "logs"),
    }
    _failed_reports[report.nodeid].append(phase)


def _source_metadata() -> dict[str, Any]:
    from app.config.logging import collect_git_metadata

    return collect_git_metadata(REPOSITORY_ROOT)


def pytest_sessionfinish(session, exitstatus: int) -> None:
    """写失败详情和每日 summary；报告失败不得改变 pytest 原退出码。

    Args:
        session：pytest 当前测试会话。
        exitstatus：pytest 已确定的原始退出码。
    """
    try:
        started = _started_at or datetime.now().astimezone()
        run_id = os.environ.get("ROLEPLEX_TEST_RUN_ID", "00000000")
        failure_files: list[str] = []
        identities = {
            nodeid: failure_identity(nodeid, _safe_case_ids.get(nodeid)) for nodeid in _failed_reports
        }
        name_counts = Counter(identity["test_name"] for identity in identities.values())
        if _failed_reports:
            failure_dir = LOG_ROOT / started.strftime("%Y-%m-%d") / "failures" / f"{started.strftime('%H-%M-%S')}_{run_id}"
            for nodeid in sorted(_failed_reports):
                identity = identities[nodeid]
                suffix = (
                    f"_{identity['nodeid_hash']}"
                    if name_counts[identity["test_name"]] > 1 or "[" in nodeid
                    else ""
                )
                filename = f"{identity['test_name']}{suffix}.json"
                request_ids = sorted(set(_REQUEST_ID.findall(json.dumps(_failed_reports[nodeid], ensure_ascii=False))))
                _atomic_json(
                    failure_dir / filename,
                    {
                        "schema_version": 2,
                        "run_id": run_id,
                        **identity,
                        "failures": _failed_reports[nodeid],
                        "request_ids": request_ids,
                    },
                )
                failure_files.append(filename)

        terminal = session.config.pluginmanager.get_plugin("terminalreporter")
        stats = terminal.stats if terminal is not None else {}
        invocation = list(session.config.invocation_params.args)
        summary: dict[str, Any] = {
            "schema_version": 2,
            "run_id": run_id,
            "source": _source_metadata(),
            **classify_test_scope(invocation),
            "started_at": started.isoformat(),
            "ended_at": datetime.now().astimezone().isoformat(),
            "duration_ms": round((time.monotonic() - _started_monotonic) * 1000, 2),
            "status": "passed" if exitstatus == 0 else ("interrupted" if exitstatus == 2 else "failed"),
            "exit_code": int(exitstatus),
            "collected": int(getattr(session, "testscollected", 0)),
            "passed": len(stats.get("passed", [])),
            "failed": len(_failed_reports),
            "errors": len({nodeid for nodeid, phases in _failed_reports.items() if any(p["phase"] != "call" for p in phases)}),
            "skipped": len(stats.get("skipped", [])),
            "xfailed": len(stats.get("xfailed", [])),
            "xpassed": len(stats.get("xpassed", [])),
            "deselected": len(stats.get("deselected", [])),
            "database": database_display_path(os.environ.get("DATABASE_URL", "")),
            "failure_files": failure_files,
        }
        if failure_files:
            summary["failure_dir"] = f"failures/{started.strftime('%H-%M-%S')}_{run_id}"
        _append_jsonl(LOG_ROOT / started.strftime("%Y-%m-%d") / "summary.jsonl", summary)
    except Exception as exc:
        print(f"pytest 日志 summary 写入失败：{exc}", file=sys.stderr)
