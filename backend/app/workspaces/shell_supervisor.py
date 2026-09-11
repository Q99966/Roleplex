"""Linux 单次 Shell 子进程监管器；作为隔离解释器入口，不导入后端设置或凭据。"""
import ctypes
import os
import signal
import subprocess
import sys
import time
import threading
import json
from pathlib import Path

import psutil

if __package__:
    from .process_identity import birth_identity
else:
    # 隔离解释器只显式加入这个可信代码目录，不读取工作区模块或后端配置/凭据。
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from process_identity import birth_identity


class StopRequested(BaseException):
    """父宿主请求停止；只在受信任监管进程内处理。"""


def write_receipt(path: str, runtime_id: str, token: bytes, exit_code: int) -> None:
    """原子保存已发生的回收证明，后端消失后也能核查；不输出任何证明正文。

    Args:
        path：宿主指定的私有凭据路径，不能由用户脚本指定。
        runtime_id：本次随机运行身份。
        token：独立管道交付的随机证明令牌，用户 Shell 不继承该管道。
        exit_code：被监管 Shell 的退出结果。
    """
    target = Path(path)
    temporary = target.with_suffix('.tmp')
    created = False
    try:
        target.parent.mkdir(mode=0o700, exist_ok=True)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump({'runtime_id': runtime_id, 'pid': os.getpid(), 'birth': birth_identity(os.getpid()),
                'token': token.hex(), 'verified': True, 'exit_code': exit_code}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        # 不覆盖同名未知证明；链接只有在文件完整写好后才对恢复端可见。
        os.link(temporary, target)
        temporary.unlink()
        directory_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError:
        # 磁盘故障不撤销已经完成的 OS 回收；缺少持久证明时恢复端必须保守拒绝释放。
        pass
    finally:
        if created:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def stop(_signum, _frame):
    """中断阻塞的 stdin/等待，进入后代清理。

    Args:
        _signum：宿主停止信号。
        _frame：当前 Python 栈，不输出。
    """
    raise StopRequested()


def reap_descendants() -> bool:
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
                return True
            if pid == 0:
                break
        if time.monotonic() >= deadline:
            return False
        time.sleep(.01)


def main() -> int:
    """先成为 subreaper，再接收脚本；不让用户代码运行在监管器中。"""
    # Linux PR_SET_CHILD_SUBREAPER。仅本进程生效；先成功设置，再允许创建 Shell。
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
        return 125
    signal.signal(signal.SIGTERM, stop)
    arguments = sys.argv[1:]
    result_fd = None
    watcher = None
    receipt_path = runtime_id = None
    receipt_token = None
    if arguments[:1] == ['--parent-fd']:
        parent_fd = int(arguments[1])
        runtime_id = arguments[3]
        # runtime ID 只作为恢复时的宿主身份，不是脚本或凭据。
        arguments = arguments[4:]
        if arguments[:1] == ['--result-fd']:
            result_fd = int(arguments[1])
            arguments = arguments[2:]
        if arguments[:1] == ['--receipt-path']:
            receipt_path = arguments[1]
            arguments = arguments[2:]
        if arguments[:1] == ['--receipt-token-fd']:
            token_fd = int(arguments[1])
            receipt_token = os.read(token_fd, 32)
            os.close(token_fd)
            arguments = arguments[2:]
            if len(receipt_token) != 32:
                return 125
        def watch_parent():
            """控制通道关闭代表后端消失，不依赖可能复用的父 PID。"""
            try:
                while os.read(parent_fd, 1):
                    pass
            finally:
                os.kill(os.getpid(), signal.SIGTERM)
        watcher = threading.Thread(target=watch_parent, daemon=True)
    code = 125
    process = None
    try:
        if watcher is not None:
            watcher.start()
        # 给宿主生成的 PowerShell UTF-8 前缀留余量；原始模型脚本仍限制为 64 KiB。
        script = sys.stdin.buffer.read(131072)
        # stdin 来自已授权父进程，实际 shell argv 也仅由宿主解析。
        process = subprocess.Popen(arguments, stdin=subprocess.PIPE)
        process.communicate(script)
        code = process.returncode
    except StopRequested:
        code = 143
    except OSError:
        code = 125
    finally:
        reaped = reap_descendants()
        if not reaped:
            # 监管器未确认所有后代退出，不能把脚本自身的成功码当成回收证明。
            code = 125
        if reaped and receipt_path is not None and receipt_token is not None:
            write_receipt(receipt_path, runtime_id, receipt_token, code)
        if result_fd is not None:
            try:
                if reaped:
                    os.write(result_fd, b'1')
            except BrokenPipeError:
                # 后端硬退出时没有读端；回收仍已完成，不能因证明无法送达重新创建进程。
                pass
            finally:
                os.close(result_fd)
    if code < 0:
        # 保留 shell 被信号结束的事实，不把负返回码取模伪装成普通退出。
        if -code not in (signal.SIGKILL, signal.SIGSTOP):
            signal.signal(-code, signal.SIG_DFL)
        os.kill(os.getpid(), -code)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
