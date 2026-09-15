"""原始版本上的有界精确替换计划；纯计算阶段不写文件。"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

MAX_REPLACEMENTS = 32
MAX_FRAGMENT_BYTES = 65536


class ReplacementInput(BaseModel):
    """单个精确替换片段，不允许空旧文本或额外字段。"""
    model_config = ConfigDict(extra='forbid', strict=True, hide_input_in_errors=True)
    old_text: str = Field(min_length=1, max_length=MAX_FRAGMENT_BYTES, description="原始文件中非空且唯一匹配的旧片段，不依赖其他替换的新内容。")
    new_text: str = Field(max_length=MAX_FRAGMENT_BYTES, description="新片段，可为空字符串表示删除；新旧片段均保留原换行。")


def validate_edit_shape(value: dict) -> dict:
    """检查编辑输入形式，拒绝缺省值掩盖的新旧字段混用。

    Args:
        value：未填默认值的原始字段，按键存在性拒绝两种形式混用。
    """
    if not isinstance(value, dict):
        raise ValueError('Invalid edit shape')
    if 'replacements' in value:
        if value['replacements'] is None or 'old_text' in value or 'new_text' in value:
            raise ValueError('Mixed edit modes')
    elif value.get('old_text') is None or value.get('new_text') is None:
        raise ValueError('Missing edit fragments')
    return value


def normalize_pairs(old_text, new_text, replacements) -> list[dict]:
    """统一旧、新形式并校验整个文件节点的片段额度。

    Args:
        old_text：兼容单片段旧文本。
        new_text：兼容单片段新文本。
        replacements：新多片段输入，整节点共用正文额度。
    """
    from .files import WorkspaceFileError
    if replacements is not None and (old_text is not None or new_text is not None):
        raise WorkspaceFileError('WORKSPACE_EDIT_ARGUMENT_INVALID')
    pairs = replacements if replacements is not None else [{'old_text': old_text, 'new_text': new_text}]
    if not isinstance(pairs, list) or not 1 <= len(pairs) <= MAX_REPLACEMENTS:
        raise WorkspaceFileError('WORKSPACE_EDIT_ARGUMENT_INVALID')
    size = 0
    for pair in pairs:
        if not isinstance(pair, dict) or set(pair) != {'old_text', 'new_text'} or not isinstance(pair['old_text'], str) or not pair['old_text'] or not isinstance(pair['new_text'], str):
            raise WorkspaceFileError('WORKSPACE_EDIT_ARGUMENT_INVALID')
        try:
            size += len(pair['old_text'].encode()) + len(pair['new_text'].encode())
        except UnicodeError:
            raise WorkspaceFileError('WORKSPACE_EDIT_ARGUMENT_INVALID') from None
    if size > MAX_FRAGMENT_BYTES:
        raise WorkspaceFileError('WORKSPACE_EDIT_INPUT_TOO_LARGE')
    return pairs


def apply_replacements(current: bytes, pairs: list[dict], *, indexed: bool) -> bytes:
    """在原始版本中定位全部区间，通过校验后构造最终内容。

    Args:
        current：已校验 hash 的原始文件，所有匹配共用此版本。
        pairs：已校验数量和正文额度的片段。
        indexed：多片段错误携带序号，旧形式保持原错误结构。
    """
    from .files import WorkspaceFileError, MAX_FILE_BYTES
    try:
        text = current.decode('utf-8')
    except UnicodeDecodeError:
        raise WorkspaceFileError('WORKSPACE_FILE_NOT_TEXT') from None
    intervals = []
    for index, pair in enumerate(pairs, 1):
        old = pair['old_text']
        start = text.find(old)
        details = {'replacement_index': index, 'recovery': 'reread_and_adjust'} if indexed else None
        if start < 0:
            raise WorkspaceFileError('WORKSPACE_EDIT_MATCH_NOT_FOUND', details)
        # 从下一字符检查，避免非重叠计数漏掉 aaa 中两处 aa。
        if text.find(old, start + 1) >= 0:
            raise WorkspaceFileError('WORKSPACE_EDIT_MATCH_AMBIGUOUS', details)
        intervals.append((start, start + len(old), index, pair['new_text']))
    intervals.sort(key=lambda item: item[0])
    for previous, current_interval in zip(intervals, intervals[1:]):
        if current_interval[0] < previous[1]:
            raise WorkspaceFileError('WORKSPACE_EDIT_OVERLAP', {'replacement_index': current_interval[2],
                'conflicting_replacement_index': previous[2], 'recovery': 'split_non_overlapping'})
    fragments, offset = [], 0
    for start, end, _, replacement in intervals:
        fragments.extend((text[offset:start], replacement))
        offset = end
    fragments.append(text[offset:])
    encoded = ''.join(fragments).encode('utf-8')
    if len(encoded) > MAX_FILE_BYTES:
        raise WorkspaceFileError('WORKSPACE_FILE_TOO_LARGE')
    return encoded
