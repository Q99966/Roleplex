"""Owner 业务详情的显式采集边界；不供机器日志使用。"""
from __future__ import annotations

import json
import re
from typing import Any

CAPTURE_LIMIT = 65_536
_FIELDS = {
    'workspace_service_status': ('runtime_id', 'cursor', 'limit'),
    'workspace_list': ('path', 'after_name', 'limit'),
    'workspace_read': ('path', 'offset_bytes', 'max_bytes', 'items', 'start_line', 'end_line', 'expected_sha256'),
    'workspace_search': ('query', 'queries', 'match', 'mode', 'path', 'limit', 'context_lines'),
    'workspace_write': ('path', 'content', 'expected_sha256', 'items'),
    'workspace_edit': ('path', 'old_text', 'new_text', 'expected_sha256', 'items', 'replacements'),
    'workspace_run_command': ('command', 'args'),
}
_ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)')
_CONTROLS = re.compile(r'[\x00-\x08\x0b-\x1f\x7f]')


def bounded_text(text: str, limit: int = CAPTURE_LIMIT) -> dict[str, Any]:
    """将正文限制为 UTF-8 前缀并去除终端控制符。

    Args:
        text：只允许进入加密业务记录的内存文本。
        limit：本份正文剩余字节预算。
    """
    data = text.encode('utf-8', errors='replace')
    visible = data[:limit].decode('utf-8', errors='ignore')
    visible = _CONTROLS.sub('', _ANSI.sub('', visible))
    return {'text': visible, 'bytes': len(data), 'truncated': len(data) > limit}


def replacement_sizes(value: list) -> list[dict]:
    """Args:
        value：仅在有界 Owner 输入中保存每项字节数，不保存旧/新源码。
    """
    return [{'index': index, **{f'{name}_bytes': len(pair[name].encode('utf-8', errors='replace'))
        for name in ('old_text', 'new_text') if isinstance(pair.get(name), str)}}
        for index, pair in enumerate(value[:32], 1) if isinstance(pair, dict)]


def capture_input(tool_name: str, value: Any) -> dict[str, Any] | None:
    """只采集当前内置工作区工具的显式输入字段。

    Args:
        tool_name：防腐层识别的实际工具。
        value：框架输入，未知对象不序列化。
    """
    if tool_name not in _FIELDS or not isinstance(value, dict):
        return None
    selected = {}
    fields = ('items',) if tool_name in {'workspace_write', 'workspace_edit'} and 'items' in value else _FIELDS[tool_name]
    for key in fields:
        item = value.get(key)
        if key == 'replacements' and isinstance(item, list):
            selected['replacement_count'] = len(item)
            selected['replacements'] = replacement_sizes(item)
        elif key == 'queries' and isinstance(item, list):
            selected[key] = [term for term in item[:8] if isinstance(term, str)]
        elif key == 'items' and isinstance(item, list) and (tool_name in {'workspace_write', 'workspace_edit'} or len(item) <= 8):
            # 修改批次只保留目标和版本，不重复保存整批源码；未知嵌套字段默认排除。
            allowed = {'path', 'expected_sha256'} if tool_name in {'workspace_write', 'workspace_edit'} else {'path', 'offset_bytes', 'max_bytes', 'start_line', 'end_line', 'expected_sha256'}
            selected[key] = [{name: value for name, value in row.items()
                if name in allowed and (value is None or isinstance(value, str) or type(value) is int)}
                for row in item if isinstance(row, dict)]
            if tool_name in {'workspace_write', 'workspace_edit'}:
                selected = {'item_count': len(item), **selected}
                for target, source in zip(selected[key], (row for row in item if isinstance(row, dict))):
                    if tool_name == 'workspace_edit' and isinstance(source.get('replacements'), list):
                        target['replacement_count'] = len(source['replacements'])
                        target['replacements'] = replacement_sizes(source['replacements'])
                    for name in ('content',) if tool_name == 'workspace_write' else ('old_text', 'new_text'):
                        if isinstance(source.get(name), str):
                            target[f'{name}_bytes'] = len(source[name].encode('utf-8', errors='replace'))
        elif key == 'args' and isinstance(item, dict):
            selected[key] = {'path': item['path']} if isinstance(item.get('path'), str) else {}
        elif item is None or isinstance(item, (str, bool)) or (type(item) is int and abs(item) < 2**63):
            if key in value:
                selected[key] = item
    return bounded_text(json.dumps(selected, ensure_ascii=False, indent=2))


def capture_output(tool_name: str, value: Any) -> dict[str, Any] | None:
    """采集已登记工具的字符串结果，拒绝隐式 repr 未知对象。

    Args:
        tool_name：防腐层识别的实际工具。
        value：框架返回的文本或 ToolMessage。
    """
    if tool_name == 'workspace_run_shell':
        return _capture_shell_output(value)
    if tool_name not in _FIELDS:
        return None
    content = getattr(value, 'content', value)
    return bounded_text(content) if isinstance(content, str) else None


def _capture_shell_output(value: Any) -> dict[str, Any] | None:
    """只提取 Shell 运行器的明确字段，双流限额不截坏结构化结果。

    Args:
        value：执行层返回的 JSON 文本或 ToolMessage，不隐式 repr 任意对象。
    """
    content = getattr(value, 'content', value)
    if not isinstance(content, str):
        return None
    from .tools import FAILED_OUTPUT_PREFIX, REJECTED_OUTPUT_PREFIX
    for prefix in (FAILED_OUTPUT_PREFIX, REJECTED_OUTPUT_PREFIX):
        if content.startswith(prefix):
            content = content[len(prefix):].strip()
            break
    try:
        result = json.loads(content)
    except (ValueError, TypeError):
        return None
    if not isinstance(result, dict):
        return None
    if not all(isinstance(result.get(name), str) for name in ('stdout', 'stderr')):
        refused = result.get('error_code') in {'SHELL_REJECTED', 'SHELL_APPROVAL_EXPIRED', 'SHELL_ARGUMENT_INVALID',
            'SHELL_NOT_SUPPORTED', 'SHELL_REQUEST_CONFLICT', 'SHELL_APPROVAL_MISMATCH', 'WORKSPACE_TOOL_NOT_AVAILABLE'}
        return {'format': 'shell-v1', 'stdout': None, 'stderr': None, 'execution_duration_ms': None,
            'execution_status': 'not_executed' if refused else 'unavailable', 'exit_code': None}
    lengths = {name: len(result[name].encode('utf-8')) for name in ('stdout', 'stderr')}
    # 双流共享预算；小流先保证保留，大流分享剩余，避免大量 stdout 吞掉全部 stderr。
    out_limit = min(lengths['stdout'], CAPTURE_LIMIT // 2 + max(0, CAPTURE_LIMIT // 2 - lengths['stderr']))
    captures = {}
    for name, limit in [('stdout', out_limit), ('stderr', CAPTURE_LIMIT - out_limit)]:
        capture = bounded_text(result[name], limit)
        reported = result.get(name + '_bytes')
        if type(reported) is int and reported >= capture['bytes']:
            capture['truncated'] = capture['truncated'] or reported > capture['bytes']
            capture['bytes'] = reported
        captures[name] = capture
    duration = result.get('duration_ms')
    return {'format': 'shell-v1', **captures,
        'execution_duration_ms': duration if type(duration) is int and duration >= 0 else None,
        'execution_status': result.get('status') if result.get('status') in {'exited', 'timed_out', 'cancelled'} else 'unavailable',
        'exit_code': result.get('exit_code') if type(result.get('exit_code')) is int else None}
