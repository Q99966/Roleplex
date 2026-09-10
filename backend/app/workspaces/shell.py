"""解析部署允许的 Shell；只有审批宿主可以调用执行入口。"""
import os
import shutil
import sys
from pathlib import Path

from ..config import settings
from .commands import WorkspaceCommandError, run_process


def shell_configuration() -> dict:
    """冻结实际 Shell 与限制，不启动进程，也不接受模型路径。"""
    kind = settings.workspace_shell_kind
    if kind == 'auto':
        kind = 'powershell' if os.name == 'nt' else 'bash'
    candidates = ('bash',) if kind == 'bash' else ('pwsh', 'powershell')
    if os.name == 'nt':
        candidates = tuple(name + '.exe' for name in candidates)
    executable = next((found for name in candidates if (found := shutil.which(name))), None)
    if executable is None or (os.name != 'nt' and sys.platform != 'linux'):
        raise WorkspaceCommandError('SHELL_NOT_SUPPORTED')
    argv = [str(Path(executable).resolve()), *(['--noprofile', '--norc', '-s'] if kind == 'bash'
        else ['-NoProfile', '-NonInteractive', '-Command', '-'])]
    return {'shell_kind': kind, 'argv': argv, 'timeout_seconds': settings.workspace_command_timeout_seconds,
        'output_bytes': settings.workspace_command_output_bytes}


def validate_script(script: str) -> None:
    """拒绝空、超限或无效编码的脚本，不保存其错误原文。

    Args:
        script：只允许进入审批密文或 stdin 的脚本。
    """
    try:
        valid = isinstance(script, str) and bool(script.strip()) and '\0' not in script and len(script.encode('utf-8')) <= 65536
    except UnicodeError:
        valid = False
    if not valid:
        raise WorkspaceCommandError('SHELL_ARGUMENT_INVALID')


async def run_shell(request: dict) -> dict:
    """复用 W1b 有界运行器，脚本只通过 stdin 交付。

    Args:
        request：审批宿主已重新解密、校验并授权的冻结执行上下文。
    """
    script = request['script']
    if request['shell_kind'] == 'powershell':
        script = ('[Console]::InputEncoding = [System.Text.UTF8Encoding]::new(); '
                  '[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); $OutputEncoding = [Console]::OutputEncoding\n') + script
    supervised = sys.platform == 'linux'
    argv = tuple(request['argv'])
    if supervised:
        argv = (str(Path(sys.executable).resolve()), '-I', '-X', 'utf8', str(Path(__file__).with_name('shell_supervisor.py').resolve()), *argv)
    return await run_process(argv, root=Path(request['root_path']), payload=(script + '\n').encode('utf-8'),
        timeout=request['timeout_seconds'], output_limit=request['output_bytes'], graceful_cleanup=supervised)
