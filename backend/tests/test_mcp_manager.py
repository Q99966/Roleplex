"""Windows stdio MCP 生命周期测试。

使用仓库内的受控 stub server，不访问网络、不触碰真实用户文件。验证四件事：
命令解析能找到 Windows 下的 `.cmd` 包装器、一次调用往返正常、调用超时可控、
以及关闭后子进程树被彻底回收（SDK 自身只会终止直接子进程）。
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import psutil
import pytest

from app.mcp.manager import McpManager, McpServerConfig, resolve_command

STUB_SERVER = Path(__file__).resolve().parent / "mcp_stub_server.py"
OWNER_ID = 1

# 真实 npx server 依赖网络与 npm 缓存，默认不跑；需要人工验证时设置该环境变量。
_RUN_NPX = os.environ.get("ROLEPLEX_MCP_NPX") == "1"


def _stub_config() -> McpServerConfig:
    """直接用当前解释器启动 stub server 的配置。"""
    return McpServerConfig(name="stub", command=sys.executable, args=(str(STUB_SERVER),))


def _write_cmd_wrapper(directory: Path) -> Path:
    """生成一个启动 stub server 的 .cmd 包装器，模拟 npx 这类批处理入口。"""
    wrapper = directory / "roleplex_stub.cmd"
    wrapper.write_text(f'@echo off\r\n"{sys.executable}" "{STUB_SERVER}" %*\r\n', encoding="ascii")
    return wrapper


def _text_of(result) -> str:
    """从工具调用结果里取出纯文本内容。"""
    return "".join(getattr(block, "text", "") for block in result.content)


@pytest.mark.anyio
async def test_call_tool_roundtrip():
    """连接建立后应能列出工具并完成一次调用往返。"""
    manager = McpManager(initialize_timeout=30.0, call_timeout=10.0)
    config = _stub_config()
    try:
        tools = await manager.list_tools(OWNER_ID, config)
        assert {tool.name for tool in tools.tools} >= {"echo", "block"}

        result = await manager.call_tool(OWNER_ID, config, "echo", {"text": "你好"})
        assert _text_of(result) == "echo:你好"
    finally:
        await manager.shutdown()


@pytest.mark.anyio
async def test_call_timeout_is_reported_to_caller():
    """工具卡住时调用方必须拿到超时，而不是永久挂起。"""
    manager = McpManager(initialize_timeout=30.0, call_timeout=0.5)
    config = _stub_config()
    try:
        with pytest.raises(asyncio.TimeoutError):
            await manager.call_tool(OWNER_ID, config, "block", {"seconds": 30.0})
    finally:
        await manager.shutdown()


@pytest.mark.anyio
async def test_missing_command_fails_fast():
    """命令不存在时应快速失败并给出可定位的错误。"""
    manager = McpManager(initialize_timeout=10.0)
    config = McpServerConfig(name="missing", command="roleplex-not-installed-command")
    try:
        with pytest.raises(FileNotFoundError):
            await manager.list_tools(OWNER_ID, config)
    finally:
        await manager.shutdown()


@pytest.mark.skipif(sys.platform != "win32", reason="仅验证 Windows 的 .cmd 包装器行为")
def test_resolve_command_finds_cmd_wrapper(tmp_path, monkeypatch):
    """裸命令名必须能解析到 .cmd 包装器：底层子进程 API 不会自动补全扩展名。"""
    wrapper = _write_cmd_wrapper(tmp_path)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    resolved = resolve_command(wrapper.stem)

    assert Path(resolved) == wrapper
    assert resolved.lower().endswith(".cmd")


@pytest.mark.anyio
@pytest.mark.skipif(sys.platform != "win32", reason="仅验证 Windows 的 .cmd 包装器行为")
async def test_shutdown_kills_whole_process_tree(tmp_path, monkeypatch):
    """通过 .cmd 包装器启动时，关闭必须回收孙进程，不能留下孤儿。"""
    wrapper = _write_cmd_wrapper(tmp_path)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    manager = McpManager(initialize_timeout=30.0, call_timeout=10.0)
    config = McpServerConfig(name="stub-cmd", command=wrapper.stem)

    result = await manager.call_tool(OWNER_ID, config, "echo", {"text": "tree"})
    assert _text_of(result) == "echo:tree"

    # 读取内部状态确认进程树形态：包装器进程之下还有真正的服务进程。
    host = manager._hosts[(OWNER_ID, config.fingerprint())]
    assert host.pid is not None
    tree_pids = [host.pid] + [child.pid for child in psutil.Process(host.pid).children(recursive=True)]
    assert len(tree_pids) >= 2, "期望 .cmd 包装器之下还有子进程，否则这条用例失去意义"

    await manager.shutdown()
    await asyncio.sleep(0.2)

    alive = [pid for pid in tree_pids if psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE]
    assert alive == [], f"关闭后仍有存活进程：{alive}"


@pytest.mark.anyio
@pytest.mark.skipif(not _RUN_NPX, reason="需要网络与 npm 缓存，设置 ROLEPLEX_MCP_NPX=1 后手动运行")
async def test_real_npx_server_can_start(tmp_path):
    """人工验证：真实 npx server 首次拉包可能耗时分钟级，初始化超时必须足够长。"""
    manager = McpManager(initialize_timeout=300.0, call_timeout=60.0)
    config = McpServerConfig(
        name="filesystem",
        command="npx",
        args=("-y", "@modelcontextprotocol/server-filesystem", str(tmp_path)),
    )
    try:
        tools = await manager.list_tools(OWNER_ID, config)
        assert tools.tools, "真实 server 应至少暴露一个工具"
    finally:
        await manager.shutdown()
