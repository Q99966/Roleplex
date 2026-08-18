"""stdio MCP 服务连接管理（Windows 生命周期 spike）。

本模块只负责连接与调用的生命周期，不参与产品页面。三个必须自管的点：

1. **宿主任务模型**：`stdio_client` 与 `ClientSession` 的进入和退出必须发生在同一个
   asyncio 任务里，否则底层 anyio 的取消作用域会跨任务退出而报错。因此每个 server
   有一个常驻宿主任务，外部通过队列提交请求、用 Future 取回结果。
2. **Windows 可执行文件解析**：锁定版本的 mcp SDK 直接把命令名交给
   `anyio.open_process`，不做任何 `.cmd` 解析，Windows 下 `npx` 这类包装器会启动失败，
   所以启动前必须自己解析出真实路径。
3. **进程树清理**：SDK 退出时只终止直接子进程；`.cmd` 包装器或 `npx` 还会派生孙进程，
   只靠 SDK 会留下孤儿进程，必须按进程树清理。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from typing import Any

import psutil
from mcp import ClientSession, StdioServerParameters, stdio_client
from mcp.client.stdio import get_default_environment

logger = logging.getLogger("roleplex.mcp")

# 首次启动可能需要下载依赖（例如 npx 拉包），因此初始化超时远大于调用超时。
DEFAULT_INITIALIZE_TIMEOUT = 180.0
DEFAULT_CALL_TIMEOUT = 60.0

# 子进程需要但 SDK 默认继承列表里没有的变量：缺少它们时 Windows 下 .cmd 解析和中文输出都会出问题。
_EXTRA_INHERITED_ENV_VARS = ("PATHEXT", "COMSPEC", "WINDIR", "NUMBER_OF_PROCESSORS", "PROGRAMDATA")


@dataclass(frozen=True)
class McpServerConfig:
    """一个 stdio MCP server 的启动配置。"""

    name: str
    command: str
    args: tuple[str, ...] = ()
    env: tuple[tuple[str, str], ...] = ()

    def fingerprint(self) -> str:
        """返回配置指纹，用于连接池键；相同配置复用同一个子进程。"""
        payload = json.dumps(
            {"command": self.command, "args": list(self.args), "env": sorted(self.env)},
            ensure_ascii=False, sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def resolve_command(command: str) -> str:
    """解析命令的真实可执行路径。

    Args:
        command：配置里的命令名或路径。

    Returns:
        可直接交给子进程 API 的绝对路径。Windows 下 `shutil.which` 会按 `PATHEXT`
        找到 `.cmd`/`.bat` 包装器，这一步不能省略。

    Raises:
        FileNotFoundError：命令在 PATH 中不存在。
    """
    resolved = shutil.which(command)
    if resolved is None:
        raise FileNotFoundError(f"找不到可执行文件：{command}")
    return resolved


def build_env(extra: tuple[tuple[str, str], ...] = ()) -> dict[str, str]:
    """构造子进程环境变量。

    只继承运行必需的系统变量，再补上 SDK 默认列表缺失的几项，最后合并调用方显式提供的项。
    返回值可能包含服务自身需要的凭据，**不得整体写入日志**。
    """
    env = get_default_environment()
    for key in _EXTRA_INHERITED_ENV_VARS:
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.update(dict(extra))
    return env


def kill_process_tree(pid: int | None, *, timeout: float = 5.0) -> None:
    """终止进程及其全部子孙进程。

    Args:
        pid：目标进程号，None 表示没有需要清理的进程。
        timeout：等待优雅退出的秒数，超时后强制杀死。

    SDK 只会终止直接子进程，`.cmd` 包装器和 npx 派生出的真实服务进程会变成孤儿，
    因此这里按进程树清理。目标进程已经退出属于正常情况，不视为错误。
    """
    if pid is None:
        return
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    victims = parent.children(recursive=True) + [parent]
    for victim in victims:
        try:
            victim.terminate()
        except psutil.NoSuchProcess:
            continue
    _, alive = psutil.wait_procs(victims, timeout=timeout)
    for victim in alive:
        try:
            victim.kill()
        except psutil.NoSuchProcess:
            continue


@dataclass
class _Request:
    """提交给宿主任务的一次调用。"""

    action: str
    payload: dict[str, Any]
    future: asyncio.Future
    timeout: float


@dataclass
class _ServerHost:
    """一个 server 的常驻宿主任务状态。"""

    config: McpServerConfig
    initialize_timeout: float
    call_timeout: float
    requests: asyncio.Queue[_Request] = field(default_factory=asyncio.Queue)
    ready: asyncio.Future = field(default_factory=asyncio.Future)
    task: asyncio.Task | None = None
    pid: int | None = None

    async def run(self) -> None:
        """在单个任务内完成子进程的打开、服务与关闭。

        所有可能失败的步骤都必须在同一个 try 内：命令解析、进程启动和初始化任何一步
        抛错，都要通过 `ready` 通知等待方，否则调用方会永远挂在等待初始化上。
        """
        before: set[int] = set()
        try:
            params = StdioServerParameters(
                command=resolve_command(self.config.command),
                args=list(self.config.args),
                env=build_env(self.config.env),
            )
            # 记录启动元数据即可，环境变量与参数可能含凭据，不进日志。
            logger.info("mcp.server_starting", extra={"server": self.config.name, "command": params.command})
            before = {child.pid for child in psutil.Process().children()}
            async with stdio_client(params) as (read_stream, write_stream):
                self.pid = next(iter({child.pid for child in psutil.Process().children()} - before), None)
                async with ClientSession(read_stream, write_stream) as session:
                    await asyncio.wait_for(session.initialize(), timeout=self.initialize_timeout)
                    if not self.ready.done():
                        self.ready.set_result(None)
                    await self._serve(session)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self.ready.done():
                self.ready.set_exception(exc)
            logger.warning(
                "mcp.server_failed",
                extra={"server": self.config.name, "error_type": type(exc).__name__},
            )
        finally:
            kill_process_tree(self.pid)
            logger.info("mcp.server_stopped", extra={"server": self.config.name, "pid": self.pid})

    def fail_ready_if_pending(self, task: asyncio.Task) -> None:
        """宿主任务结束但没人解决 `ready` 时唤醒等待方，避免调用方永久挂起。"""
        if self.ready.done():
            return
        if task.cancelled():
            self.ready.cancel()
            return
        self.ready.set_exception(task.exception() or RuntimeError("MCP 宿主任务提前结束"))

    async def _serve(self, session: ClientSession) -> None:
        """顺序消费请求队列，把结果回传给调用方。"""
        while True:
            request = await self.requests.get()
            try:
                if request.action == "call_tool":
                    result = await asyncio.wait_for(
                        session.call_tool(request.payload["name"], request.payload.get("arguments")),
                        timeout=request.timeout,
                    )
                elif request.action == "list_tools":
                    result = await asyncio.wait_for(session.list_tools(), timeout=request.timeout)
                else:
                    raise ValueError(f"未知的 MCP 请求类型：{request.action}")
            except asyncio.CancelledError:
                if not request.future.done():
                    request.future.cancel()
                raise
            except Exception as exc:
                if not request.future.done():
                    request.future.set_exception(exc)
            else:
                if not request.future.done():
                    request.future.set_result(result)


class McpManager:
    """按 (Owner, 配置指纹) 复用 stdio MCP 子进程的连接池。

    配置 MCP 等价于授予本机代码执行权，因此连接池键包含 Owner，即使当前只有单 Owner
    也不共享给其他主体。
    """

    def __init__(
        self,
        *,
        initialize_timeout: float = DEFAULT_INITIALIZE_TIMEOUT,
        call_timeout: float = DEFAULT_CALL_TIMEOUT,
    ) -> None:
        self._hosts: dict[tuple[int, str], _ServerHost] = {}
        self._lock = asyncio.Lock()
        self._initialize_timeout = initialize_timeout
        self._call_timeout = call_timeout

    async def _host_for(self, owner_id: int, config: McpServerConfig) -> _ServerHost:
        """取出或启动该配置对应的宿主任务，并等待初始化完成。"""
        key = (owner_id, config.fingerprint())
        async with self._lock:
            host = self._hosts.get(key)
            if host is None or (host.task is not None and host.task.done()):
                host = _ServerHost(
                    config=config,
                    initialize_timeout=self._initialize_timeout,
                    call_timeout=self._call_timeout,
                )
                host.task = asyncio.create_task(host.run())
                host.task.add_done_callback(host.fail_ready_if_pending)
                self._hosts[key] = host
        await host.ready
        return host

    async def _submit(self, host: _ServerHost, action: str, payload: dict[str, Any], timeout: float) -> Any:
        """把一次请求交给宿主任务并等待结果。"""
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        await host.requests.put(_Request(action=action, payload=payload, future=future, timeout=timeout))
        return await future

    async def list_tools(self, owner_id: int, config: McpServerConfig, *, timeout: float | None = None) -> Any:
        """返回该 server 暴露的工具列表。"""
        host = await self._host_for(owner_id, config)
        return await self._submit(host, "list_tools", {}, timeout or self._call_timeout)

    async def call_tool(
        self,
        owner_id: int,
        config: McpServerConfig,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        """调用一个 MCP 工具。

        Args:
            owner_id：配置所属 Owner，参与连接池隔离。
            config：server 启动配置。
            name：工具名。
            arguments：工具参数。
            timeout：本次调用超时秒数，默认使用管理器的调用超时。

        Returns:
            SDK 返回的调用结果。

        Raises:
            asyncio.TimeoutError：调用超时；调用方需要决定重试还是重建连接。
        """
        host = await self._host_for(owner_id, config)
        return await self._submit(host, "call_tool", {"name": name, "arguments": arguments}, timeout or self._call_timeout)

    async def close(self, owner_id: int, config: McpServerConfig) -> None:
        """关闭单个 server 的宿主任务与进程树。"""
        key = (owner_id, config.fingerprint())
        async with self._lock:
            host = self._hosts.pop(key, None)
        await self._stop(host)

    async def shutdown(self) -> None:
        """关闭全部 server；服务停止时必须调用，避免留下孤儿子进程。"""
        async with self._lock:
            hosts = list(self._hosts.values())
            self._hosts.clear()
        for host in hosts:
            await self._stop(host)

    async def _stop(self, host: _ServerHost | None) -> None:
        """取消宿主任务并等待它完成清理。

        必须先杀进程树再取消任务：SDK 的清理会等待子进程自然退出，而卡在长任务里的
        服务不会主动退出，先取消再清理会让关闭一直挂着。
        """
        if host is None or host.task is None:
            return
        kill_process_tree(host.pid)
        host.task.cancel()
        try:
            await host.task
        except asyncio.CancelledError:
            pass
        # 宿主任务可能在记录 pid 之前就被取消，这里兜底再确认一次。
        kill_process_tree(host.pid)
