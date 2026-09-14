"""T2 离线扫描基准：只生成占位文件，报告预算、耗时与 Python 分配峰值，不调用模型。"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import sys
import time
import tracemalloc
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def main() -> None:
    """在类别/时间戳目录内验证候选预算与大文件扫描，只保留最近五轮。"""
    from app.config import settings
    from app.workspaces.files import WorkspaceFileService
    from app.workspaces.batch_read import read_many, ReadBatchReceipt
    base = Path(__file__).resolve().parents[2] / 'data' / 'scan-benchmarks'
    root = base / datetime.now().strftime('%Y%m%d%H%M%S')
    root.mkdir(parents=True, exist_ok=False)
    rounds = sorted(path for path in base.iterdir() if re.fullmatch(r'\d{14}', path.name) and path.is_dir() and not path.is_symlink())
    for old in rounds[:-5]:
        shutil.rmtree(old)
    files = root / 'files'
    files.mkdir()
    for name in ['a.txt', 'b.txt']:
        (files / name).write_text('x' * 20480)
    line = 'padding-' + 'x' * 55 + '\n'
    (files / 'large.txt').write_text(line * 32768 + 'TARGET_FUNCTION\nkeep\n', encoding='utf-8')
    service = WorkspaceFileService(root=files, execution_id='benchmark')
    reports = []

    async def measure(label, operation):
        """Args:
            label：固定基准阶段名。
            operation：不含网络或用户内容的受控调用。
        """
        tracemalloc.start()
        started = time.perf_counter()
        value = await operation()
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        encoded = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(',', ':'))
        reports.append({'case': label, 'duration_ms': elapsed, 'python_peak_bytes': peak, 'json_bytes': len(encoded.encode())})
        return value

    async def authorize():
        """底层基准不模拟数据库耗时或伪造权限验证数据。"""
        return service

    original = settings.workspace_read_content_bytes
    try:
        for budget in [32768, 65536]:
            settings.workspace_read_content_bytes = budget
            items = [{'path': 'a.txt', 'max_bytes': 32768}, {'path': 'b.txt', 'max_bytes': 32768}]
            value = await measure(f'two_files_{budget}', lambda: read_many(items, authorize=authorize, receipt=ReadBatchReceipt(items)))
            value = json.loads(value)
            reports[-1].update(status=value['status'], returned_bytes=sum(node['result']['bytes'] for node in value['items'] if node['result']),
                               complete_files=sum(bool(node['result'] and node['result']['eof']) for node in value['items']))
        value = await measure('large_line_range', lambda: service.read_lines('large.txt', start_line=32769, end_line=32770))
        reports[-1].update(returned_bytes=value['bytes'], scanned_bytes=value['scanned_bytes'])
        value = await measure('large_text_search', lambda: service.search(query='TARGET_FUNCTION', path='large.txt'))
        reports[-1].update(status=value['status'], matches=len(value['matches']), scanned_bytes=value['scanned_bytes'])
    finally:
        settings.workspace_read_content_bytes = original
    (root / 'report.json').write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'report': str(root / 'report.json'), 'results': reports}, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
