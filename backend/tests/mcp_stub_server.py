"""受控的 stdio MCP 测试服务器。

仅供测试使用：它不访问真实用户文件，只提供回显和可控阻塞两个工具，
用来验证连接生命周期、调用超时、取消和进程树清理。
"""
from __future__ import annotations

import time

from mcp.server.fastmcp import FastMCP

server = FastMCP("roleplex-stub")


@server.tool()
def echo(text: str) -> str:
    """回显传入文本，用于验证一次完整的调用往返。"""
    return f"echo:{text}"


@server.tool()
def block(seconds: float = 3600.0) -> str:
    """阻塞指定秒数，用于验证客户端调用超时。"""
    time.sleep(seconds)
    return "done"


if __name__ == "__main__":
    server.run()
