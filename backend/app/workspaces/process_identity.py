"""进程出生身份适配；Linux 使用内核启动 tick，避免墙钟调整造成身份误判。"""
from pathlib import Path
import sys

import psutil


def birth_identity(pid: int) -> str:
    """Args:
        pid：实际创建或正在核查的进程，不据此直接授予终止权限。
    """
    if sys.platform != 'linux':
        return str(psutil.Process(pid).create_time())
    try:
        with Path(f'/proc/{pid}/stat').open('rb') as stream:
            content = stream.read(4096)
        # comm 可以包含空格/括号，从最后一个右括号后的字段读取 starttime（第 22 字段）。
        fields = content[content.rfind(b')') + 1:].split()
        return f'linux:{int(fields[19])}'
    except FileNotFoundError:
        raise psutil.NoSuchProcess(pid) from None
    except PermissionError:
        raise psutil.AccessDenied(pid) from None
    except (IndexError, ValueError):
        raise OSError('Process identity unavailable') from None


def same_birth(pid: int, expected: str | None) -> bool:
    """兼容旧记录的 epoch 字符串，但新 Linux 记录不再依赖墙钟。

    Args:
        pid：当前进程 ID。
        expected：登记时保存的出生身份。
    """
    if expected is None:
        return False
    actual = birth_identity(pid) if expected.startswith('linux:') else str(psutil.Process(pid).create_time())
    return actual == expected
