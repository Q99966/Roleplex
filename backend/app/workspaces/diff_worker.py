"""隔离的差异计算进程：只读 stdin，不读取宿主文件，不执行传入文本。"""
import difflib
import json
import sys

OUTPUT_LIMIT = 65536
LINE_LIMIT = 1000


def file_lines(text: str) -> list[str]:
    """只按 LF 划分物理行，保留 CRLF 与未换行结尾。

    Args:
        text：有效 UTF-8 解码结果。
    """
    parts = text.split('\n')
    return [part + '\n' for part in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def build_diff(result: dict, before: str, after: str) -> dict:
    """产生项目结构化 hunk，精确计量整份私有对象，不截坏行或 JSON。

    Args:
        result：已经限长的文件身份及提交事实。
        before：提交前文本。
        after：确认提交的文本。
    """
    old, new = file_lines(before), file_lines(after)
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    opcodes = matcher.get_opcodes()
    file = result['files'][0]
    file['added'] = sum(b - a for tag, _, _, a, b in opcodes if tag in ('insert', 'replace'))
    file['removed'] = sum(j - i for tag, i, j, _, _ in opcodes if tag in ('delete', 'replace'))
    hunks = file['hunks']
    used = len(json.dumps(result, ensure_ascii=False).encode())
    line_count = 0
    for group in matcher.get_grouped_opcodes(3):
        hunk = {'old_start': group[0][1] + 1, 'old_lines': group[-1][2] - group[0][1],
                'new_start': group[0][3] + 1, 'new_lines': group[-1][4] - group[0][3], 'lines': []}
        overhead = len(json.dumps(hunk).encode()) + (2 if hunks else 0)
        if used + overhead > OUTPUT_LIMIT - 64:
            result['availability'] = 'partial'
            break
        hunks.append(hunk)
        used += overhead
        for tag, i, j, a, b in group:
            indices = ([('context', n, a + n - i, old[n]) for n in range(i, j)] if tag == 'equal' else
                       [('delete', n, None, old[n]) for n in range(i, j)] + [('insert', None, n, new[n]) for n in range(a, b)])
            for kind, old_index, new_index, text in indices:
                ending = 'crlf' if text.endswith('\r\n') else 'lf' if text.endswith('\n') else 'none'
                content = text[:-2] if ending == 'crlf' else text[:-1] if ending == 'lf' else text
                line = {'kind': kind, 'old_line': old_index + 1 if old_index is not None else None,
                        'new_line': new_index + 1 if new_index is not None else None, 'text': content, 'ending': ending}
                size = len(json.dumps(line, ensure_ascii=False).encode()) + (2 if hunk['lines'] else 0)
                if line_count == LINE_LIMIT or used + size > OUTPUT_LIMIT - 64:
                    result['availability'] = 'partial'
                    break
                hunk['lines'].append(line)
                used += size
                line_count += 1
            if result['availability'] == 'partial':
                break
        if not hunk['lines']:
            hunks.pop()
        if result['availability'] == 'partial':
            break
    if len(json.dumps(result, ensure_ascii=False).encode()) > OUTPUT_LIMIT:
        file['hunks'] = []
        result['availability'] = 'partial'
    return result


def main() -> None:
    """限制 IPC 输入与输出，异常不回显私有原文。"""
    try:
        raw = sys.stdin.buffer.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError()
        request = json.loads(raw)
        result = build_diff(request['metadata'], request['before'], request['after'])
        encoded = json.dumps(result, ensure_ascii=False).encode()
        if len(encoded) > OUTPUT_LIMIT:
            raise ValueError()
        sys.stdout.buffer.write(encoded)
    except Exception:
        sys.exit(2)


if __name__ == '__main__':
    main()
