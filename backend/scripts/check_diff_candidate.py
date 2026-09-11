"""G2 独立 difflib 选型试验：只生成占位内容，不读取项目文件或接入产品调度。"""
from __future__ import annotations

import difflib
import hashlib
import json
import multiprocessing as mp
import queue
import time

INPUT_BYTES = 256 * 1024
OUTPUT_BYTES = 64 * 1024
MAX_LINES = 10_000
DISPLAY_LINES = 1000


def worker(pipe, before: str, after: str) -> None:
    """在可终止的独立进程计算差异，只返回有界安全统计。

    Args:
        pipe：本次私有 IPC，只返回统计而非源文。
        before：生成的旧占位文本。
        after：生成的新占位文本。
    """
    started = time.perf_counter()
    old, new = before.splitlines(keepends=True), after.splitlines(keepends=True)
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    opcodes = matcher.get_opcodes()
    # 检查算法操作确能重构目标，不以增删计数代替正确性。
    rebuilt = []
    for tag, i, j, a, b in opcodes:
        rebuilt.extend(old[i:j] if tag == 'equal' else new[a:b] if tag in ('insert', 'replace') else [])
    assert ''.join(rebuilt) == after
    added = sum(b - a for tag, _, _, a, b in opcodes if tag in ('insert', 'replace'))
    removed = sum(j - i for tag, i, j, _, _ in opcodes if tag in ('delete', 'replace'))
    compute_ms = (time.perf_counter() - started) * 1000
    serial_started = time.perf_counter()
    result = {'format': 'spike-only', 'added': added, 'removed': removed, 'partial': False, 'lines': []}
    count = 0
    serialized_bytes = len(json.dumps(result, ensure_ascii=False).encode())
    for group in matcher.get_grouped_opcodes(3):
        for tag, i, j, a, b in group:
            rows = [('context', n + 1, old[n]) for n in range(i, j)] if tag == 'equal' else (
                [('delete', n + 1, old[n]) for n in range(i, j)] + [('insert', n + 1, new[n]) for n in range(a, b)])
            for row in rows:
                row_bytes = len(json.dumps(row, ensure_ascii=False).encode()) + (2 if count else 0)
                if count >= DISPLAY_LINES or serialized_bytes + row_bytes > OUTPUT_BYTES - 64:
                    result['partial'] = True
                    break
                result['lines'].append(row)
                serialized_bytes += row_bytes
                count += 1
            if result['partial']:
                break
        if result['partial']:
            break
    data = json.dumps(result, ensure_ascii=False).encode()
    assert len(data) <= OUTPUT_BYTES
    pipe.send({'status': 'partial' if result['partial'] else 'complete', 'correct': True,
               'added': added, 'removed': removed, 'compute_ms': round(compute_ms, 2),
               'serialize_ms': round((time.perf_counter() - serial_started) * 1000, 2),
               'output_bytes': len(data), 'display_lines': count,
               'result_hash': hashlib.sha256(data).hexdigest()})
    pipe.close()


def measure(name: str, before: str, after: str, *, deadline: float = 1.0, cancel: bool = False) -> dict:
    """对子进程施加真实截止与回收，预算不足在启动前拒绝。

    Args:
        name：非敏感固定样本名。
        before：占位旧文本。
        after：占位新文本。
        deadline：包含进程启动与序列化的试验截止秒数。
        cancel：模拟调用方取消，验证不会遗留计算进程。
    """
    size = len(before.encode()) + len(after.encode())
    row = {'sample': name, 'input_bytes': size}
    if size > INPUT_BYTES or len(before.splitlines()) + len(after.splitlines()) > MAX_LINES:
        return {**row, 'status': 'input_budget', 'process_started': False}
    ctx = mp.get_context('spawn')
    reader, writer = ctx.Pipe(duplex=False)
    process = ctx.Process(target=worker, args=(writer, before, after))
    started = time.perf_counter()
    process.start()
    writer.close()
    try:
        if cancel:
            result = {'status': 'cancelled'}
        elif reader.poll(max(0, deadline - (time.perf_counter() - started))):
            result = reader.recv()
        else:
            result = {'status': 'deadline'}
    finally:
        if process.is_alive():
            process.terminate()
        process.join(1)
        if process.is_alive():
            process.kill()
            process.join(1)
        reaped = not process.is_alive()
        reader.close()
        if reaped:
            process.close()
    assert reaped
    return {**row, **result, 'process_started': True, 'reaped': reaped,
            'wall_ms': round((time.perf_counter() - started) * 1000, 2)}


def main() -> None:
    """输出可重复、无源文的基准结果；本脚本不是产品 diff 实现。"""
    source = ''.join(f'const value_{i:05d} = "占位-{i:05d}";\n' for i in range(3000))
    small = source[:120 * 1024]
    medium = 'a' * (130 * 1024)
    large = 'a' * (1024 * 1024 - 1)
    repetitive = 'same\n' * 4900
    cases = [
        ('created', '', '<h1>占位🙂</h1>\n'), ('empty', '', ''), ('unchanged', '甲\n乙\n', '甲\n乙\n'),
        ('line_endings', '甲\r\n乙', '甲\n丙\n'), ('unicode_controls', '<script>占位</script>\n\x1b[31m', '<script>🙂</script>\n\u202e'),
        ('code_small_change', small, small.replace('value_00003', 'value_new03', 1)),
        ('130k_small_change', medium, medium[:20] + 'b' + medium[21:]),
        ('near_1m_small_change', large, large[:20] + 'b' + large[21:]),
        ('repetitive', repetitive, 'other\n' + repetitive[:-5]),
        ('full_replace', ''.join(f'a{i}\n' for i in range(1800)), ''.join(f'b{i}\n' for i in range(1800))),
        ('long_line', 'a' * 80000, 'b' * 80000),
    ]
    results = [measure(*case) for case in cases]
    results.append(measure('cancelled_worker', repetitive, repetitive + 'other', cancel=True))
    print(json.dumps({'candidate': 'python-difflib', 'input_limit': INPUT_BYTES, 'output_limit': OUTPUT_BYTES,
                      'admission_trial': admission_trial(), 'results': results}, ensure_ascii=False))


def admission_trial() -> dict:
    """用标准库验证两计算/四等待的候选边界，仅为试验，不接入会话队列或产品运行器。"""
    ctx = mp.get_context('spawn')
    waiting = queue.Queue(maxsize=4)
    active = []
    started = time.perf_counter()
    payload = 'same\n' * 4900
    weight = len(payload.encode()) * 2
    pending_bytes = 0
    rejected = 0
    expired = 0
    for index in range(7):
        if index < 2:
            reader, writer = ctx.Pipe(duplex=False)
            process = ctx.Process(target=worker, args=(writer, payload, 'other\n' + payload[:-5]))
            process.start()
            writer.close()
            active.append((process, reader))
        else:
            try:
                waiting.put_nowait((time.perf_counter(), payload, weight))
                pending_bytes += weight
            except queue.Full:
                rejected += 1
    high_water = pending_bytes
    # 在本样本计算占满槽位时，等待按期限降级，不能越过并发数无限启动进程。
    try:
        while not waiting.empty():
            submitted, _, retained = waiting.queue[0]
            if time.perf_counter() - submitted >= .25:
                waiting.get_nowait()
                pending_bytes -= retained
                expired += 1
            else:
                time.sleep(.005)
    finally:
        for process, reader in active:
            if process.is_alive():
                process.terminate()
            process.join(1)
            if process.is_alive():
                process.kill()
                process.join(1)
            assert not process.is_alive()
            reader.close()
            process.close()
    assert rejected == 1 and expired == 4 and pending_bytes == 0
    return {'scope': 'isolated-spike-only', 'active_limit': 2, 'queued_limit': 4, 'rejected': rejected,
            'expired': expired, 'pending_bytes_peak': high_water, 'pending_bytes_after': pending_bytes,
            'all_workers_reaped': True, 'wall_ms': round((time.perf_counter() - started) * 1000, 2)}


if __name__ == '__main__':
    main()
