"""Linux 单次 Shell 子进程监管器；作为隔离解释器入口，不导入后端设置或凭据。"""
import ctypes
import os
import signal
import subprocess
import sys
import time

import psutil


class StopRequested(BaseException):
    """父宿主请求停止；只在受信任监管进程内处理。"""


def stop(_signum, _frame):
    """中断阻塞的 stdin/等待，进入后代清理。

    Args:
        _signum：宿主停止信号。
        _frame：当前 Python 栈，不输出。
    """
    raise StopRequested()


def reap_descendants() -> None:
    """清理仍存活和被收养的后代，setsid 后也不能遗留普通运行进程。"""
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    parent = psutil.Process()
    deadline = time.monotonic() + 1.5
    while True:
        children = parent.children(recursive=True)
        for child in reversed(children):
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        while True:
            try:
                pid, _status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return
            if pid == 0:
                break
        if time.monotonic() >= deadline:
            return
        time.sleep(.01)


def main() -> int:
    """先成为 subreaper，再接收脚本；不让用户代码运行在监管器中。"""
    # Linux PR_SET_CHILD_SUBREAPER。仅本进程生效；先成功设置，再允许创建 Shell。
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
        return 125
    signal.signal(signal.SIGTERM, stop)
    code = 125
    process = None
    try:
        # 给宿主生成的 PowerShell UTF-8 前缀留余量；原始模型脚本仍限制为 64 KiB。
        script = sys.stdin.buffer.read(131072)
        # stdin 来自已授权父进程，实际 shell argv 也仅由宿主解析。
        process = subprocess.Popen(sys.argv[1:], stdin=subprocess.PIPE)
        process.communicate(script)
        code = process.returncode
    except StopRequested:
        code = 143
    except OSError:
        code = 125
    finally:
        reap_descendants()
    if code < 0:
        # 保留 shell 被信号结束的事实，不把负返回码取模伪装成普通退出。
        if -code not in (signal.SIGKILL, signal.SIGSTOP):
            signal.signal(-code, signal.SIG_DFL)
        os.kill(os.getpid(), -code)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
