"""规范化上下文层和工具策略的不可逆指纹。"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def stable_hash(value: str | Any) -> str:
    """对字符串或 canonical JSON 计算 SHA-256。

    Args:
        value：稳定文本或只含可序列化配置的对象。
    """
    if isinstance(value, str):
        encoded = value.encode("utf-8")
    else:
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
        ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
