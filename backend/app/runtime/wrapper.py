"""用独占管道监视 POSIX 启动包装器，避免后端成为孤儿进程。"""
import asyncio
import os
import signal

from ..config.logging import set_process_stop_reason


def watch_wrapper():
    """注册非阻塞管道观察器，返回关闭函数；直启及 Windows 不注册。

    管道只由包装器持有写端，EOF 是包装器已退出的证据；不用可复用的 PID 猜测。
    只发一次正常退出信号，避免 Uvicorn 把第二次信号解释成强制退出。
    """
    value = os.environ.pop('ROLEPLEX_WRAPPER_FD', None)
    if os.name != 'posix' or value is None:
        return lambda: None
    fd = int(value)
    loop = asyncio.get_running_loop()
    os.set_blocking(fd, False)
    closed = False

    def close():
        """移除观察器并关闭本进程的管道端，重复调用无副作用。"""
        nonlocal closed
        if not closed:
            closed = True
            loop.remove_reader(fd)
            os.close(fd)

    def readable():
        """退出请求或 EOF 均转为后端正常关闭，不在回调中执行资源回收。"""
        try:
            data = os.read(fd, 1)
        except BlockingIOError:
            return
        close()
        set_process_stop_reason('wrapper_shutdown' if data else 'wrapper_lost')
        os.kill(os.getpid(), signal.SIGINT)

    loop.add_reader(fd, readable)
    return close
