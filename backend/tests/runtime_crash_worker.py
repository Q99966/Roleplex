"""仅供 W1d 硬退出测试的真实后端宿主，不打印 Token、脚本或服务输出。"""
import asyncio
import json
from pathlib import Path
import sys

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / 'tests'))


async def main():
    """在外部本轮目录启动经审批服务，随后等待测试端强制终止本宿主。"""
    from test_workspace_commands import command_conversation
    from test_runtime_service import enable_service, launch
    from app.runtime.registry import get
    import psutil
    root = Path(sys.argv[1]).resolve()
    if 'testworkspace' not in root.parts or not root.is_dir():
        raise SystemExit('Invalid runtime crash fixture')
    async with command_conversation(root) as (client, headers, cid, rid, wid):
        await enable_service(client, headers, rid, wid)
        row = await launch(client, headers, cid)
        saved = await get(row['id'])
        children = psutil.Process(saved.pid).children(recursive=True)
        print(json.dumps({'id': row['id'], 'pid': saved.pid, 'port': row['port'],
            'children': [{'pid': child.pid, 'birth': str(child.create_time())} for child in children]}), flush=True)
        await asyncio.Event().wait()


if __name__ == '__main__':
    asyncio.run(main())
