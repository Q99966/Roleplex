"""仅供 W1b E2E 的固定进程 profile，不由产品工具注册或导入。"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    """运行测试服务器选择的 profile；不接受模型脚本或任意 argv。"""
    sys.stdin.buffer.read()
    profile = sys.argv[1]
    if profile == 'output':
        sys.stdout.write('测试输出\n' * 20000)
        sys.stderr.write('受控诊断\n' * 20000)
        return 0
    if profile == 'exit':
        return 7
    if profile in {'timeout', 'cancel'}:
        child = subprocess.Popen([sys.executable, '-I', '-c', 'import time; time.sleep(60)'])
        Path(f'{profile}-pids.txt').write_text(f'{os.getpid()},{child.pid}', encoding='utf-8')
        time.sleep(60)
        return 0
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
