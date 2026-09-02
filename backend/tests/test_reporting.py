"""pytest 日志 v2 汇总与失败文件的纯函数测试。"""
from __future__ import annotations

from types import SimpleNamespace


def test_failure_identity_never_persists_raw_parameterized_nodeid():
    """参数可能含凭据，持久化身份只能使用文件、函数、安全 case 和 hash。"""
    from reporting import failure_identity

    raw = "tests/test_api.py::test_request[https://host/path?token=secret-value]"
    identity = failure_identity(raw, safe_case_ids=None)

    assert identity["test_file"] == "tests/test_api.py"
    assert identity["test_name"] == "request"
    assert len(identity["nodeid_hash"]) == 8
    assert "parameter_ids" not in identity
    assert "secret-value" not in repr(identity)


def test_capture_keeps_tail_and_reports_original_size():
    """失败诊断保留异常附近的尾部，并报告截断前后字节数。"""
    from reporting import bounded_capture

    result = bounded_capture("开头" * 100 + "最终异常", 32, "logs")

    assert result["logs_truncated"] is True
    assert result["logs_original_bytes"] > result["logs_stored_bytes"]
    assert result["captured_logs"].endswith("最终异常")


def test_scope_distinguishes_full_and_partial_without_persisting_keyword():
    """默认测试入口才算 full；-k 等选择器只保存 hash，不保存原表达式。"""
    from reporting import classify_test_scope

    assert classify_test_scope(["-q"])["test_scope"] == "full"
    assert classify_test_scope(["tests", "-q"])["test_scope"] == "full"
    partial = classify_test_scope(["tests/test_worlds.py", "-k", "token=secret"])
    assert partial["test_scope"] == "partial"
    assert partial["selection"] == ["tests/test_worlds.py"]
    assert "selection_hash" in partial
    assert "token=secret" not in repr(partial)
    assert classify_test_scope(["-kprovider", "-q"])["test_scope"] == "partial"


def test_database_summary_uses_repository_relative_path():
    """summary 不暴露开发机绝对目录，仓库内数据库使用相对路径。"""
    from reporting import REPOSITORY_ROOT, database_display_path

    repo_db = (REPOSITORY_ROOT / "data" / "roleplex-test.db").as_posix()
    assert database_display_path(f"sqlite+aiosqlite:///{repo_db}") == "data/roleplex-test.db"
    assert database_display_path("sqlite+aiosqlite:////outside/path/roleplex-test.db") == "roleplex-test.db"


def test_assertion_failure_records_structured_exception_type():
    """断言消息没有类名前缀时，也应从 pytest excinfo 保存 AssertionError。"""
    import reporting
    from reporting import pytest_runtest_logreport, pytest_runtest_makereport

    report = SimpleNamespace(
        nodeid="tests/test_example.py::test_value",
        failed=True,
        when="call",
        longreprtext="E assert 2 == 1",
        longrepr=SimpleNamespace(reprcrash=SimpleNamespace(message="assert 2 == 1")),
        capstdout="",
        capstderr="",
        caplog="",
    )
    call = SimpleNamespace(excinfo=SimpleNamespace(typename="AssertionError"))
    outcome = SimpleNamespace(get_result=lambda: report)

    hook = pytest_runtest_makereport(None, call)
    next(hook)
    try:
        hook.send(outcome)
    except StopIteration:
        pass

    reporting._failed_reports.clear()
    try:
        pytest_runtest_logreport(report)
        phase = reporting._failed_reports[report.nodeid][0]
        assert phase["exception_type"] == "AssertionError"
    finally:
        reporting._failed_reports.clear()
