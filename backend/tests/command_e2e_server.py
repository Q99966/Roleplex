"""W1b 独立 fake World 测试服务器；受控 profile 只存在于此测试入口。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))


def main() -> None:
    """在独立 World 上运行真实前后端链路，并安装测试专属进程 adapter。"""
    stamp = os.environ['ROLEPLEX_E2E_STAMP']
    if not stamp.isdigit():
        raise RuntimeError('Invalid command E2E stamp')
    os.environ.pop('DATABASE_URL', None)
    os.environ['WORLDS_DIR'] = str(BACKEND_ROOT.parent / 'data' / f'roleplex-command-e2e-{stamp}')
    os.environ['ROLEPLEX_WORLD'] = 'default'
    os.environ['AGENT_USE_FAKE_PROVIDER'] = 'true'
    os.environ['WORKSPACE_COMMAND_TIMEOUT_SECONDS'] = '3'
    os.environ['WORKSPACE_COMMAND_OUTPUT_BYTES'] = '1024'
    from app.workspaces import commands
    from app.main import app
    import uvicorn
    original = commands.run_process
    # Playwright 在 webServer 启动后才运行 globalSetup；这里只固定本轮路径，不读取目录。
    allowed_root = Path(os.environ['ROLEPLEX_COMMAND_E2E_WORKSPACE']).resolve()

    async def process_profile(argv, **kwargs):
        """只为该轮测试根和固定文件选择受控 profile。

        Args:
            argv：产品固定 worker 参数。
            kwargs：产品校验后的 cwd、控制输入及限制。
        """
        payload = json.loads(kwargs['payload'])
        name = payload['args'].get('path')
        if kwargs['root'] == allowed_root and payload['command'] == 'read' and name in {
            'output.txt', 'exit.txt', 'timeout.txt', 'cancel.txt',
        }:
            argv = (sys.executable, '-I', '-X', 'utf8', str(Path(__file__).with_name('command_process_fixture.py')), name[:-4])
        return await original(argv, **kwargs)

    commands.run_process = process_profile
    uvicorn.run(app, host='127.0.0.1', port=8005)


if __name__ == '__main__':
    main()
