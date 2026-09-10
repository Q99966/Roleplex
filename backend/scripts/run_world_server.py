"""管理世界切换的 Uvicorn 包装器进程。"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.worlds.manager import validate_world_name  # noqa: E402
from app.worlds import WorldManager  # noqa: E402
from app.worlds.compatibility import WorldRequiresNewerRoleplex, assert_world_compatible  # noqa: E402

DEFAULT_WORLDS_DIR = BACKEND_DIR.parent / "worlds"


def build_parser() -> argparse.ArgumentParser:
    """构造包装器参数。"""
    parser = argparse.ArgumentParser(description="启动可切换世界的 Roleplex 后端")
    parser.add_argument("--world", default="default")
    parser.add_argument("--worlds-dir", default=str(DEFAULT_WORLDS_DIR))
    parser.add_argument("--ensure-world", action="append", default=[], help="启动前确保额外世界存在，可重复")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    return parser


def run(argv: list[str] | None = None) -> int:
    """循环启动后端；收到受控切换请求时使用目标世界重启。

    Args:
        argv：命令行参数；省略时读取进程参数。
    """
    args = build_parser().parse_args(argv)
    current = validate_world_name(args.world)
    worlds_dir = Path(args.worlds_dir).resolve()
    manager = WorldManager(worlds_dir)
    manager.ensure(current)
    for name in args.ensure_world:
        manager.ensure(validate_world_name(name))
    with tempfile.TemporaryDirectory(prefix="roleplex-world-wrapper-") as temporary:
        control_file = Path(temporary) / "switch-target"
        while True:
            world = manager.ensure(current)
            try:
                assert_world_compatible(world.database_path)
            except WorldRequiresNewerRoleplex as exc:
                print(f"无法启动世界 {current}：{exc}", file=sys.stderr)
                return 2
            environment = dict(os.environ)
            # 包装器必须进入世界托管模式，不能继承调用终端残留的测试数据库地址。
            environment.pop("DATABASE_URL", None)
            environment.update({
                "ROLEPLEX_WORLD": current,
                "WORLDS_DIR": str(worlds_dir),
                "WORLD_CONTROL_FILE": str(control_file),
            })
            command = [
                sys.executable, "-m", "uvicorn", "app.main:app",
                "--host", args.host, "--port", str(args.port),
            ]
            read_fd, write_fd = os.pipe() if os.name == 'posix' else (None, None)
            if read_fd is not None:
                environment['ROLEPLEX_WRAPPER_FD'] = str(read_fd)
            stopped = False
            process = None

            def request_stop(signum, frame):
                """转发一次退出请求，后续信号不能截断后端回收。

                Args:
                    signum：包装器收到的终止信号。
                    frame：Python 信号处理器上下文，不使用。
                """
                nonlocal stopped
                if stopped:
                    return
                stopped = True
                if write_fd is not None:
                    try:
                        os.write(write_fd, b'S')
                    except BrokenPipeError:
                        pass
                elif process is not None and process.poll() is None:
                    process.send_signal(signal.CTRL_BREAK_EVENT)

            previous = {sig: signal.signal(sig, request_stop) for sig in (signal.SIGINT, signal.SIGTERM)}
            try:
                process = subprocess.Popen(command, cwd=BACKEND_DIR, env=environment,
                    **({'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else
                       {'start_new_session': True, 'pass_fds': (read_fd,)}))
                if read_fd is not None:
                    os.close(read_fd)
                    read_fd = None
                elif stopped:
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                exit_code = process.wait()
            finally:
                for fd in (read_fd, write_fd):
                    if fd is not None:
                        os.close(fd)
                for sig, handler in previous.items():
                    signal.signal(sig, handler)
            if stopped or not control_file.is_file():
                return exit_code
            target = validate_world_name(control_file.read_text(encoding="utf-8"))
            control_file.unlink(missing_ok=True)
            current = target
            print(f"切换世界并重启后端：{current}", flush=True)


if __name__ == "__main__":
    raise SystemExit(run())
