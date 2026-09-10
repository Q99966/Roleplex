"""Owner 业务详情的显式采集边界；不供机器日志使用。"""
from __future__ import annotations

import json
import re
from typing import Any

CAPTURE_LIMIT = 65_536
_FIELDS = {
    'workspace_list': ('path', 'after_name', 'limit'),
    'workspace_read': ('path', 'offset_bytes', 'max_bytes'),
    'workspace_write': ('path', 'content', 'expected_sha256'),
    'workspace_run_command': ('command', 'args'),
}
_ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)')
_CONTROLS = re.compile(r'[\x00-\x08\x0b-\x1f\x7f]')


def bounded_text(text: str) -> dict[str, Any]:
    """将正文限制为 UTF-8 前缀并去除终端控制符。

    Args:
        text：只允许进入加密业务记录的内存文本。
    """
    data = text.encode('utf-8', errors='replace')
    visible = data[:CAPTURE_LIMIT].decode('utf-8', errors='ignore')
    visible = _CONTROLS.sub('', _ANSI.sub('', visible))
    return {'text': visible, 'bytes': len(data), 'truncated': len(data) > CAPTURE_LIMIT}


def capture_input(tool_name: str, value: Any) -> dict[str, Any] | None:
    """只采集当前内置工作区工具的显式输入字段。

    Args:
        tool_name：防腐层识别的实际工具。
        value：框架输入，未知对象不序列化。
    """
    if tool_name not in _FIELDS or not isinstance(value, dict):
        return None
    selected = {}
    for key in _FIELDS[tool_name]:
        item = value.get(key)
        if key == 'args' and isinstance(item, dict):
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
    if tool_name not in _FIELDS:
        return None
    content = getattr(value, 'content', value)
    return bounded_text(content) if isinstance(content, str) else None
