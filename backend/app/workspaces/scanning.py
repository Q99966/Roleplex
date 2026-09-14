"""流式扫描、按行读取和字面搜索；输入不进入 Shell，正文不写入日志。"""
from __future__ import annotations

import asyncio
import codecs
import fnmatch
import hashlib
import json
import os
import stat
from collections import deque
from time import monotonic

from ..config import settings
from .paths import WorkspacePathError, is_link_like, normalize_relative_path

CHUNK_BYTES = 65536
JSON_LIMIT = 65536
MAX_LINES = 2000
EXCLUDED = frozenset({'node_modules', '.venv', 'venv', '__pycache__', '.cache', 'dist', 'build', 'coverage', '.next'})


def encode(value: dict) -> str:
    """Args:
        value：有界模型/Owner 结果，不供机器日志使用。
    """
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


class ScanBudget:
    """一次宿主调用的扫描计量，批次共享剩余字节与截止时间。"""

    def __init__(self):
        """冻结本次主机预算；模型不能延长已开始的扫描。"""
        self.scanned = 0
        self.limit = settings.workspace_scan_total_bytes
        self.started = monotonic()
        self.seconds = settings.workspace_scan_seconds
        self.deadline = self.started + self.seconds

    def check(self, size: int = 0) -> None:
        """Args:
            size：即将加入本调用的实际扫描字节。
        """
        from .files import WorkspaceFileError
        self.scanned += size
        if self.scanned > self.limit:
            raise WorkspaceFileError('WORKSPACE_SCAN_LIMIT_EXCEEDED',
                {'phase': 'scanning', 'actual': self.scanned, 'limit': self.limit, 'unit': 'bytes'})
        if monotonic() >= self.deadline:
            raise WorkspaceFileError('WORKSPACE_SCAN_LIMIT_EXCEEDED',
                {'phase': 'scanning', 'actual': round(monotonic() - self.started, 3), 'limit': self.seconds, 'unit': 'seconds'})


class FileScan:
    """单文件完整扫描后才交付 hash，文件变化不产生可编辑版本凭据。"""

    def __init__(self, service, path: str, budget: ScanBudget):
        """Args:
            service：已经绑定宿主根的文件服务。
            path：再次经过路径安全检查的相对路径。
            budget：本调用共享扫描预算。
        """
        self.service, self.path, self.budget = service, path, budget
        self.sha256 = None
        self.scanned_bytes = 0
        self.line_offset = 0

    async def chunks(self):
        """以小块校验 UTF-8、hash 和稳定身份，取消时关闭真实文件描述符。"""
        from .files import WorkspaceFileError
        self.budget.check()
        target = self.service._resolve(self.path)
        before = target.stat()
        if not stat.S_ISREG(before.st_mode):
            raise WorkspaceFileError('WORKSPACE_FILE_NOT_TEXT')
        if before.st_size > settings.workspace_scan_file_bytes:
            raise WorkspaceFileError('WORKSPACE_FILE_TOO_LARGE', {'phase': 'precheck', 'actual': before.st_size,
                'limit': settings.workspace_scan_file_bytes, 'unit': 'bytes'})
        digest = hashlib.sha256()
        decoder = codecs.getincrementaldecoder('utf-8')('strict')
        fingerprint = lambda value: (value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        try:
            # NOFOLLOW 防止最后路径分量被替换，NONBLOCK 避免普通文件检查后换成 FIFO 时挂住。
            fd = os.open(target, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0))
            with os.fdopen(fd, 'rb') as stream:
                opened = os.fstat(stream.fileno())
                if not stat.S_ISREG(opened.st_mode):
                    raise WorkspaceFileError('WORKSPACE_FILE_NOT_TEXT')
                if fingerprint(opened) != fingerprint(before):
                    raise WorkspaceFileError('WORKSPACE_FILE_REVISION_CONFLICT')
                while True:
                    await asyncio.sleep(0)
                    self.budget.check()
                    chunk = stream.read(min(CHUNK_BYTES, self.budget.limit - self.budget.scanned + 1))
                    if not chunk:
                        break
                    self.scanned_bytes += len(chunk)
                    from .scan_admission import record_scanned
                    record_scanned(len(chunk))
                    self.budget.check(len(chunk))
                    if self.scanned_bytes > settings.workspace_scan_file_bytes:
                        raise WorkspaceFileError('WORKSPACE_FILE_TOO_LARGE')
                    decoder.decode(chunk)
                    if b'\0' in chunk:
                        raise WorkspaceFileError('WORKSPACE_FILE_NOT_TEXT')
                    digest.update(chunk)
                    yield chunk
                decoder.decode(b'', final=True)
                current = self.service._resolve(self.path)
                if fingerprint(os.fstat(stream.fileno())) != fingerprint(before) or fingerprint(current.stat()) != fingerprint(before):
                    raise WorkspaceFileError('WORKSPACE_FILE_REVISION_CONFLICT')
        except UnicodeError:
            raise WorkspaceFileError('WORKSPACE_FILE_NOT_TEXT') from None
        except OSError:
            raise WorkspaceFileError('WORKSPACE_READ_FAILED') from None
        self.sha256 = digest.hexdigest()

    async def lines(self):
        """逐行返回 bytes 或超长标记 None，行缓冲最多一个块加一条有界行。"""
        buffer = b''
        oversized = False
        offset = length = 0
        async for chunk in self.chunks():
            pieces = chunk.split(b'\n')
            for index, piece in enumerate(pieces):
                complete = index < len(pieces) - 1
                length += len(piece) + int(complete)
                if not oversized:
                    buffer += piece + (b'\n' if complete else b'')
                    if len(buffer) > JSON_LIMIT:
                        buffer, oversized = b'', True
                if complete:
                    self.line_offset = offset
                    yield None if oversized else buffer
                    offset += length
                    length = 0
                    buffer, oversized = b'', False
        if buffer or oversized:
            self.line_offset = offset
            yield None if oversized else buffer


async def read_bytes(service, path: str, *, offset_bytes: int, max_bytes: int, expected_sha256=None,
                     budget=None, strict_budget: bool = False, json_budget: int = JSON_LIMIT) -> dict:
    """完整扫描验证版本，仅保留所需字节片段，JSON 压缩不损坏游标。

    Args:
        service：当前绑定的文件服务。
        path：相对文件路径。
        offset_bytes：UTF-8 字节边界上的起点。
        max_bytes：本项允许保留的正文额度。
        expected_sha256：可选的已知完整版本。
        budget：批次共享计量，单项可省略。
        strict_budget：批次不允许为完整字符突破剩余额度。
        json_budget：本项完整 JSON 额度。
    """
    from .files import WorkspaceFileError
    if type(offset_bytes) is not int or offset_bytes < 0 or type(max_bytes) is not int or not 1 <= max_bytes <= JSON_LIMIT:
        raise WorkspaceFileError('WORKSPACE_PATH_INVALID')
    max_bytes = min(max_bytes, settings.workspace_read_content_bytes)
    scan = FileScan(service, path, budget or ScanBudget())
    kept = bytearray()
    position = 0
    async for chunk in scan.chunks():
        left, right = max(0, offset_bytes - position), min(len(chunk), offset_bytes + max_bytes + 3 - position)
        if right > left:
            kept.extend(chunk[left:right])
        position += len(chunk)
    if expected_sha256 is not None and scan.sha256 != expected_sha256:
        raise WorkspaceFileError('WORKSPACE_FILE_REVISION_CONFLICT')
    if offset_bytes > position:
        raise WorkspaceFileError('WORKSPACE_PATH_INVALID')
    if kept and kept[0] & 0xc0 == 0x80:
        raise WorkspaceFileError('WORKSPACE_FILE_NOT_TEXT')
    text = bytes(kept[:max_bytes]).decode('utf-8', errors='ignore')
    if not text and kept:
        if strict_budget:
            raise WorkspaceFileError('WORKSPACE_READ_BUDGET_EXHAUSTED')
        text = bytes(kept).decode('utf-8', errors='ignore')[:1]
    result = {'text': text, 'bytes': len(text.encode()), 'eof': offset_bytes + len(text.encode()) == position,
              'next_offset': offset_bytes + len(text.encode()), 'sha256': scan.sha256}
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        candidate = {**result, 'text': text[:mid], 'bytes': len(text[:mid].encode()),
                     'next_offset': offset_bytes + len(text[:mid].encode()), 'eof': result['eof'] and mid == len(text)}
        if len(encode(candidate).encode()) <= json_budget:
            low = mid
        else:
            high = mid - 1
    if low < len(text):
        text = text[:low]
        result.update(text=text, bytes=len(text.encode()), next_offset=offset_bytes + len(text.encode()), eof=False)
    return result


async def read_lines(service, path: str, *, start_line: int, end_line: int | None = None, expected_sha256=None,
                     budget=None, content_budget=None, json_budget: int = JSON_LIMIT) -> dict:
    """按包含两端的行范围返回完整行，独立记录扫描量与续读位置。

    Args:
        service：绑定服务。
        path：相对路径。
        start_line：从 1 开始的行号。
        end_line：结束行，省略时最多 200 行。
        expected_sha256：已知全文件版本。
        budget：共享扫描额度。
        content_budget：批次分配的剩余正文额度。
        json_budget：完整结果保留量。
    """
    from .files import WorkspaceFileError
    end_line = start_line + 199 if end_line is None and type(start_line) is int else end_line
    if type(start_line) is not int or type(end_line) is not int or start_line < 1 or end_line < start_line or end_line - start_line >= MAX_LINES:
        raise WorkspaceFileError('WORKSPACE_READ_ARGUMENT_INVALID')
    remaining = min(settings.workspace_read_content_bytes, content_budget if content_budget is not None else JSON_LIMIT)
    scan = FileScan(service, path, budget or ScanBudget())
    lines, total, used, json_used, reason = [], 0, 0, 0, None
    start_offset = None
    async for line in scan.lines():
        total += 1
        if total == start_line:
            start_offset = scan.line_offset
        if start_line <= total <= end_line and reason is None:
            if line is None or used + len(line) > remaining:
                reason = 'line_too_long' if line is None or len(line) > settings.workspace_read_content_bytes else 'content_budget'
            elif json_used + len(json.dumps(line.decode('utf-8'), ensure_ascii=False).encode()) > json_budget - 512:
                reason = 'json_budget'
            else:
                lines.append(line)
                used += len(line)
                json_used += len(json.dumps(line.decode('utf-8'), ensure_ascii=False).encode())
    if expected_sha256 is not None and scan.sha256 != expected_sha256:
        raise WorkspaceFileError('WORKSPACE_FILE_REVISION_CONFLICT')
    next_line = start_line + len(lines)
    return {'mode': 'lines', 'text': b''.join(lines).decode('utf-8'), 'bytes': used, 'start_line': start_line,
            'start_offset': start_offset if start_offset is not None else scan.scanned_bytes,
            'end_line': next_line - 1 if lines else None, 'next_line': next_line, 'eof': next_line > total,
            'sha256': scan.sha256, 'scanned_bytes': scan.scanned_bytes, 'limited_reason': reason}


def excerpt(line: bytes, number: int, query: str = '') -> dict:
    """Args:
        line：有界的完整 UTF-8 行。
        number：实际行号。
        query：匹配词，仅用于将片段定位到命中附近，不进入日志。
    """
    text = line.decode('utf-8').rstrip('\r\n')
    start = max(0, text.find(query) - 128) if query else 0
    return {'line_number': number, 'text': text[start:start + 512], 'truncated': start > 0 or len(text) > 512}


async def search(service, *, query: str | None = None, mode: str = 'text', path: str = '.', limit: int = 100,
                 context_lines: int = 1, queries: list[str] | None = None, match: str = 'any', authorize=None) -> dict:
    """顺序有界扫描文件名或字面内容，未覆盖范围不能冒充无匹配。

    Args:
        service：已绑定的文件服务。
        query：单个字面文本或 files 模式的 fnmatch 文件名模式。
        queries：text 模式的多个字面词，与 query 互斥。
        match：any 命中任一词；all 要求同一行含全部词。
        mode：text 或 files。
        path：绑定根内的目录/文件范围。
        limit：最多返回的匹配数。
        context_lines：命中前后保留的行数。
        authorize：工具工厂的实时授权复核，不接受模型覆盖。
    """
    from .files import WorkspaceFileError
    terms = [query] if query is not None else queries
    if ((query is not None and queries is not None) or not isinstance(terms, list)
            or not 1 <= len(terms) <= 8 or any(not isinstance(term, str) or not 1 <= len(term) <= 256 for term in terms)
            or match not in ('any', 'all') or mode not in ('text', 'files')
            or (mode == 'files' and (queries is not None or match != 'any'))
            or not isinstance(path, str) or type(limit) is not int or not 1 <= limit <= 200
            or type(context_lines) is not int or not 0 <= context_lines <= 3):
        raise WorkspaceFileError('WORKSPACE_SEARCH_ARGUMENT_INVALID')
    try:
        if any(len(term.encode()) > 1024 for term in terms):
            raise WorkspaceFileError('WORKSPACE_SEARCH_ARGUMENT_INVALID')
    except UnicodeError:
        raise WorkspaceFileError('WORKSPACE_SEARCH_ARGUMENT_INVALID') from None
    if any(part in EXCLUDED for part in path.split('/')):
        raise WorkspaceFileError('WORKSPACE_PATH_INVALID')
    root = service._resolve(path, allow_root=True)
    budget = ScanBudget()
    result = {'version': 1, 'status': 'complete', 'matches': [], 'issues': [], 'truncated': False,
              'scanned_files': 0, 'scanned_bytes': 0, 'visited_entries': 0}
    path_bytes = 0
    attempted_files = 0

    def issue(code: str, target: str | None = None):
        """Args:
            code：固定未覆盖原因。
            target：已授权路径，私有结果中才可返回。
        """
        result.update(status='partial', truncated=True)
        entry = {'reason': code, 'path': target if target is None or len(target.encode()) <= 256 else None}
        if len(result['issues']) < 8 and entry not in result['issues']:
            result['issues'].append(entry)

    async def walk(directory):
        """Args:
            directory：已校验目录；限制每层条目集合和全部遍历量。
        """
        nonlocal path_bytes
        budget.check()
        relative = directory.relative_to(service.root).as_posix()
        directory = service._resolve(relative, allow_root=True)
        entries = []
        with os.scandir(directory) as iterator:
            for entry in iterator:
                result['visited_entries'] += 1
                if result['visited_entries'] > 10000:
                    issue('entry_budget')
                    break
                candidate = entry.name if relative == '.' else relative + '/' + entry.name
                try:
                    normalize_relative_path(candidate)
                    if entry.name in EXCLUDED or is_link_like(directory / entry.name):
                        continue
                    service._resolve(candidate)
                except (WorkspacePathError, WorkspaceFileError):
                    continue
                path_bytes += len(candidate.encode())
                if path_bytes > 1024 * 1024:
                    issue('memory_budget')
                    break
                entries.append(candidate)
                if result['visited_entries'] % 64 == 0:
                    await asyncio.sleep(0)
                    budget.check()
        for candidate in sorted(entries, key=lambda value: value.encode()):
            child = service._resolve(candidate)
            if child.is_dir():
                if result['visited_entries'] <= 10000 and path_bytes <= 1024 * 1024:
                    async for nested in walk(child):
                        yield nested
            elif child.is_file():
                yield candidate

    async def candidates():
        """根据单文件/目录范围使用同一条校验路径。"""
        if root.is_file():
            yield root.relative_to(service.root).as_posix()
        else:
            async for name in walk(root):
                yield name

    try:
        async for name in candidates():
            if authorize is not None and await authorize() is None:
                raise WorkspaceFileError('WORKSPACE_TOOL_NOT_AVAILABLE')
            budget.check()
            if len(result['matches']) >= limit:
                issue('match_limit')
                break
            if mode == 'files':
                if not fnmatch.fnmatchcase(name if '/' in query else name.rsplit('/', 1)[-1], query):
                    continue
                hits = [{'path': name, 'line_number': None, 'text': None, 'context_before': [], 'context_after': [],
                         'sha256': None, 'version_confirmed': False, 'truncated': False}]
            else:
                if attempted_files >= 500:
                    issue('file_budget')
                    break
                attempted_files += 1
                scan = FileScan(service, name, budget)
                hits, previous = [], deque(maxlen=context_lines)
                try:
                    number = 0
                    async for line in scan.lines():
                        number += 1
                        if line is None:
                            issue('line_too_long', name)
                            previous.clear()
                            continue
                        short = excerpt(line, number)
                        changed = False
                        for hit in hits[-context_lines:] if context_lines else []:
                            if number - hit['line_number'] <= context_lines:
                                hit['context_after'].append(short)
                                changed = True
                        decoded = line.decode('utf-8')
                        matched = [index for index, term in enumerate(terms) if term in decoded]
                        if matched and (match == 'any' or len(matched) == len(terms)):
                            changed = True
                            if len(hits) + len(result['matches']) < limit:
                                snippet = excerpt(line, number, terms[matched[0]])
                                hits.append({'path': name, **snippet, 'matched_queries': matched, 'context_before': list(previous), 'context_after': [],
                                             'sha256': None, 'version_confirmed': False})
                            else:
                                issue('match_limit')
                        previous.append(short)
                        if changed and len(encode({'matches': hits}).encode()) > JSON_LIMIT - 8192:
                            hits.pop()
                            issue('output_budget')
                    for hit in hits:
                        hit.update(sha256=scan.sha256, version_confirmed=True)
                except WorkspaceFileError as exc:
                    hits = []
                    issue(exc.code, name)
                    if exc.code == 'WORKSPACE_SCAN_LIMIT_EXCEEDED':
                        break
                finally:
                    if scan.scanned_bytes or scan.sha256 is not None:
                        result['scanned_files'] += 1
            for hit in hits:
                result['matches'].append(hit)
                if len(encode(result).encode()) > JSON_LIMIT - 8192:
                    result['matches'].pop()
                    issue('output_budget')
                    break
            if any(item['reason'] == 'output_budget' for item in result['issues']):
                break
    except WorkspaceFileError as exc:
        if exc.code == 'WORKSPACE_TOOL_NOT_AVAILABLE':
            raise
        issue(exc.code)
    except OSError:
        issue('WORKSPACE_READ_FAILED')
    result['scanned_bytes'] = budget.scanned
    return result
