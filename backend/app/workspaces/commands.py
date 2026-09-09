"""W1b 固定命令适配、最小环境与有界异步子进程生命周期。"""
from __future__ import annotations

import asyncio
from contextvars import copy_context
import json
import os
import signal
import sys
from pathlib import Path
from typing import Any

from .paths import WorkspacePathError, resolve_workspace_path, resolve_workspace_root

COMMAND_IDS = ('pwd', 'list', 'read', 'count')
_COMMAND_LOCKS: dict[str, asyncio.Lock] = {}


class WorkspaceCommandError(ValueError):
    """只携带稳定错误码，不保留宿主异常文本。"""

    def __init__(self, code: str):
        """构造工具失败。

        Args:
            code：登记的稳定错误码。
        """
        super().__init__(code)
        self.code = code


def command_environment() -> dict[str, str]:
    """仅继承系统运行路径，覆盖 UTF-8，排除后端及 Python 用户配置。"""
    allowed = ('PATH', 'SystemRoot', 'WINDIR', 'COMSPEC', 'PATHEXT')
    return {**{key: os.environ[key] for key in allowed if key in os.environ},
            'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8', 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'}


def validate_command(root: Path, command: str, args: dict[str, Any]) -> dict[str, str]:
    """校验命令专用参数并复用 W1a resolver；不允许 argv/cwd 注入。

    Args:
        root：execution 绑定的规范根。
        command：模型提交的稳定命令 ID。
        args：该命令专用参数对象。
    """
    if command not in COMMAND_IDS:
        raise WorkspaceCommandError('COMMAND_NOT_ALLOWED')
    if not isinstance(args, dict) or set(args) - ({'path'} if command != 'pwd' else set()):
        raise WorkspaceCommandError('COMMAND_ARGUMENT_INVALID')
    if command == 'pwd':
        return {}
    path = args.get('path', '.' if command == 'list' else None)
    if not isinstance(path, str) or any(char in path for char in '|><;\n\r=&`:'):
        raise WorkspaceCommandError('COMMAND_ARGUMENT_INVALID')
    # Windows 会折叠尾随点/空格；禁止这些别名和 ADS，避免绕过敏感文件名称检查。
    if any(part not in {'.', '..'} and part.endswith((' ', '.')) for part in path.split('/')):
        raise WorkspaceCommandError('COMMAND_ARGUMENT_INVALID')
    try:
        path.encode('utf-8')
    except UnicodeError:
        raise WorkspaceCommandError('COMMAND_ARGUMENT_INVALID') from None
    try:
        resolve_workspace_path(root, path, allow_root=command == 'list')
    except WorkspacePathError as exc:
        raise WorkspaceCommandError(exc.code) from None
    return {'path': path}


async def run_process(
    argv: tuple[str, ...], *, root: Path, payload: bytes, timeout: float, output_limit: int,
) -> dict[str, Any]:
    """运行服务端固定 argv，排空双流并在所有终态回收进程树。

    Args:
        argv：仅服务端 adapter 或受控测试构造的可执行路径与参数，绝不直接接收模型 argv。
        root：已授权 cwd。
        payload：通过 stdin 传递的受控 JSON，不进入进程标题或日志。
        timeout：含启动和管道排空的最大秒数。
        output_limit：双流累计保留字节上限，超限仍持续读取。
    """
    if os.name not in {'posix', 'nt'}:
        raise WorkspaceCommandError('COMMAND_NOT_SUPPORTED')
    clock = asyncio.get_running_loop().time
    started = clock()
    buffers = {'stdout': bytearray(), 'stderr': bytearray()}
    totals = {'stdout': 0, 'stderr': 0}
    sequences = {'stdout': 0, 'stderr': 0}
    retained = 0
    process = None
    job = None
    tasks: list[asyncio.Task] = []
    status = 'exited'
    spawning = asyncio.create_task(asyncio.create_subprocess_exec(
        *argv, cwd=str(root), env=command_environment(), stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        **({'start_new_session': True} if os.name == 'posix' else {}),
    ), context=copy_context())

    async def drain(name: str, stream: asyncio.StreamReader) -> None:
        """有界保留且完整排空一个流。

        Args:
            name：stdout 或 stderr。
            stream：该次子进程独占的管道。
        """
        nonlocal retained
        while chunk := await stream.read(8192):
            totals[name] += len(chunk)
            sequences[name] += 1
            keep = chunk[:max(0, output_limit - retained)]
            buffers[name].extend(keep)
            retained += len(keep)

    async def cleanup() -> None:
        """等待启动交接并回收进程组，取消不遗留尚未拿到句柄的子进程。"""
        nonlocal process
        if process is None:
            try:
                process = await spawning
            except OSError:
                return
        if os.name == 'posix':
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif job is not None:
            job.close()
        elif process.returncode is None:
            process.kill()
        await process.wait()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    try:
        async with asyncio.timeout(timeout):
            process = await asyncio.shield(spawning)
            if os.name == 'nt':
                from .windows_job import WindowsJob
                try:
                    job = WindowsJob(process.pid)
                except OSError:
                    raise WorkspaceCommandError('COMMAND_NOT_SUPPORTED') from None
            tasks = [asyncio.create_task(drain(name, getattr(process, name)), context=copy_context()) for name in buffers]
            process.stdin.write(payload)
            try:
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            process.stdin.close()
            # 等待父进程退出后立即清理后代；不能先等后代继承的管道 EOF。
            while process.returncode is None:
                await asyncio.sleep(0.01)
    except TimeoutError:
        status = 'timed_out'
    except OSError:
        raise WorkspaceCommandError('COMMAND_FAILED') from None
    finally:
        clean_task = asyncio.create_task(cleanup(), context=copy_context())
        # 即使用户重复点击停止，也必须完成回收后再向上抛取消。
        cancelled = False
        while not clean_task.done():
            try:
                await asyncio.shield(clean_task)
            except asyncio.CancelledError:
                cancelled = True
        clean_task.result()
        if cancelled:
            raise asyncio.CancelledError
    result = {
        'status': status, 'exit_code': process.returncode if status == 'exited' else None,
        **{name: bytes(data).decode('utf-8', errors='ignore') for name, data in buffers.items()},
        **{f'{name}_bytes': value for name, value in totals.items()},
        **{f'{name}_seq': value for name, value in sequences.items()},
        'truncated': sum(totals.values()) > retained,
        'duration_ms': int((clock() - started) * 1000),
    }
    if status == 'timed_out':
        result['error_code'] = 'COMMAND_TIMEOUT'
    elif process.returncode != 0:
        result['error_code'] = 'COMMAND_FAILED'
    return result


class WorkspaceCommandService:
    """只提供四种结构化命令，固定 Python 隔离 worker 不执行工作区代码。"""

    def __init__(self, *, root: Path, execution_id: str):
        """绑定经授权的 execution。

        Args:
            root：已经过执行授权的目录快照。
            execution_id：持久 execution 身份。
        """
        self.root = root
        self.execution_id = execution_id

    async def run(self, command: str, args: dict[str, Any]) -> dict[str, Any]:
        """验证并串行执行，返回模型专用结果。

        Args:
            command：固定 ID。
            args：按 ID 校验的结构化参数。
        """
        from ..config import settings
        try:
            if resolve_workspace_root(str(self.root)) != self.root:
                raise WorkspaceCommandError('WORKSPACE_ROOT_NOT_AVAILABLE')
        except WorkspacePathError as exc:
            raise WorkspaceCommandError(exc.code) from None
        validated = validate_command(self.root, command, args)
        worker = Path(__file__).with_name('command_worker.py').resolve()
        try:
            executable = Path(sys.executable).resolve(strict=True)
        except OSError:
            raise WorkspaceCommandError('COMMAND_NOT_SUPPORTED') from None
        async with _COMMAND_LOCKS.setdefault(str(self.root), asyncio.Lock()):
            result = await run_process(
                (str(executable), '-I', '-X', 'utf8', str(worker)), root=self.root,
                payload=json.dumps({'root': str(self.root), 'command': command, 'args': validated}, ensure_ascii=False).encode(),
                timeout=settings.workspace_command_timeout_seconds,
                output_limit=settings.workspace_command_output_bytes,
            )
        # worker 只输出登记的错误码，未知输出不能成为异常或日志文本。
        if result.get('error_code') == 'COMMAND_FAILED':
            from .command_worker import WORKER_ERRORS
            code = result['stderr'].strip()
            if code in WORKER_ERRORS:
                result['error_code'] = code
        return {'command': command, **result}
