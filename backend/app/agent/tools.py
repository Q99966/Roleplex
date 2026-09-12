"""工具危险分级与执行层拦截。

分级默认封闭：只有显式白名单里的内置工具是 safe，MCP 工具、有副作用的内置工具
和任何未知工具一律 dangerous。分级判断和拦截都发生在工具执行层，不依赖系统提示词、
工具名自述或模型的配合；被拒绝的调用以结构化结果回传模型，让本轮仍能正常收尾，
而不是抛异常中断整条链。
"""
from __future__ import annotations

import json
import re
import hashlib
from collections.abc import Collection, Sequence
from typing import Any

from langchain_core.tools import BaseTool
from langchain_core.callbacks import AsyncCallbackManagerForToolRun
from .tool_context import tool_call_id
from ..workspaces.catalog import WORKSPACE_FILE_TOOLS

# 显式 safe 白名单：只包含只读或纯生成类内置工具。
# 新增工具默认不在此列表内，必须经安全评审后显式加入。
SAFE_BUILTIN_TOOLS: frozenset[str] = frozenset({"create_artifact", "update_artifact", "read_artifact"})

# MCP 工具统一使用该前缀，便于审计与排查；前缀本身不授予任何权限。
MCP_TOOL_PREFIX = "mcp_"

# 工具审计采用专用允许字段，不从原始参数做“删敏感键后剩余全写”的反向过滤。
_TOOL_ARG_ALLOWLIST: dict[str, tuple[str, ...]] = {
    "create_artifact": ("kind", "language"),
    "update_artifact": ("artifact_id", "expected_version"),
    "read_artifact": ("artifact_id",),
}
_SAFE_LABEL = re.compile(r"^[A-Za-z0-9_.+-]{1,32}$")

# 被拒绝的工具结果以该前缀开头，防腐层据此把结束事件标记为 rejected。
REJECTED_OUTPUT_PREFIX = "[工具被拒绝]"
FAILED_OUTPUT_PREFIX = "[工具执行失败]"

DANGER_SAFE = "safe"
DANGER_DANGEROUS = "dangerous"


def classify_tool(name: str, *, owner_safe_overrides: Collection[str] = ()) -> str:
    """判断工具的危险级别。

    Args:
        name：工具名。
        owner_safe_overrides：Owner 在角色配置中显式标记为 safe 的工具名集合；
            只有 Owner 能配置，且只影响具体工具，不放开整个 MCP server。

    Returns:
        `safe` 或 `dangerous`；无法确认的工具一律返回 `dangerous`。
    """
    if name in {'workspace_run_shell', 'workspace_start_service'}:
        return DANGER_DANGEROUS
    if name in SAFE_BUILTIN_TOOLS:
        return DANGER_SAFE
    if name in owner_safe_overrides:
        return DANGER_SAFE
    return DANGER_DANGEROUS


class GuardedTool(BaseTool):
    """包装真实工具，在调用前按危险级别决定执行还是拒绝。

    包装层不做参数改写，只负责权限判定：这样即使模型被提示注入说服去调用危险工具，
    调用仍然在服务端被拦截。
    """

    inner: BaseTool
    danger: str = DANGER_DANGEROUS
    allow_dangerous: bool = False

    def _reject(self) -> str:
        """返回回传给模型的结构化拒绝结果。"""
        return f"{REJECTED_OUTPUT_PREFIX} 工具 {self.inner.name} 属于 {self.danger} 级别，当前调用者无权执行。"

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        if self.danger == DANGER_DANGEROUS and not self.allow_dangerous:
            return self._reject()
        return self.inner.run(kwargs or (args[0] if args else {}))

    async def _arun(self, *args: Any, run_manager: AsyncCallbackManagerForToolRun | None = None, **kwargs: Any) -> Any:
        """把宿主调用身份转换为领域上下文，模型 schema 中没有该字段。

        Args:
            args：已校验的位置参数。
            run_manager：框架注入的宿主运行上下文，与开始/结束领域事件使用相同身份。
            kwargs：已校验的模型业务参数。
        """
        if self.danger == DANGER_DANGEROUS and not self.allow_dangerous:
            return self._reject()
        binding = tool_call_id.set(str(run_manager.run_id) if run_manager else None)
        try:
            return await self.inner.arun(kwargs or (args[0] if args else {}))
        finally:
            tool_call_id.reset(binding)


def guard_tools(
    tools: Sequence[BaseTool],
    *,
    allow_dangerous: bool,
    owner_safe_overrides: Collection[str] = (),
) -> list[BaseTool]:
    """把工具集合包装为带执行层拦截的工具集合。

    Args:
        tools：原始工具，可能来自内置实现或 MCP 转换。
        allow_dangerous：本次调用链是否允许执行 dangerous 工具；Guest 触发的链路为 False。
        owner_safe_overrides：Owner 显式放开为 safe 的工具名。

    Returns:
        与入参顺序一致的包装工具列表，工具名、描述和参数 schema 保持不变，
        因此模型看到的工具契约不受影响。
    """
    guarded: list[BaseTool] = []
    for tool in tools:
        guarded.append(
            GuardedTool(
                name=tool.name,
                description=tool.description,
                args_schema=tool.args_schema,
                inner=tool,
                danger=classify_tool(tool.name, owner_safe_overrides=owner_safe_overrides),
                allow_dangerous=allow_dangerous,
                handle_validation_error=tool.handle_validation_error,
            )
        )
    return guarded


def summarize_tool_args(tool_name: str, args: Any) -> str:
    """仅提取已登记工具的安全参数；未知/MCP 工具默认不保存任何值。

    Args:
        tool_name：领域工具名，用于选择专用允许字段。
        args：框架提供的原始参数；本函数不会整体序列化它。
    """
    if not isinstance(args, dict):
        return "{}"
    if tool_name == 'workspace_run_command':
        command = args.get('command')
        return json.dumps({'command': command} if command in ('pwd', 'list', 'read', 'count') else {})
    if tool_name in WORKSPACE_FILE_TOOLS:
        if tool_name == 'workspace_read' and 'items' in args:
            items = args.get('items')
            return json.dumps({'item_count': len(items)} if isinstance(items, list) else {})
        path = args.get("path")
        summary: dict[str, int | str | bool] = {}
        if isinstance(path, str):
            summary["path_fingerprint"] = hashlib.sha256(path.encode("utf-8")).hexdigest()
        if tool_name == "workspace_list":
            limit = args.get("limit")
            if isinstance(limit, int) and not isinstance(limit, bool):
                summary["limit"] = limit
            summary["has_cursor"] = isinstance(args.get("after_name"), str)
        elif tool_name == "workspace_read":
            for key in ("offset_bytes", "max_bytes"):
                value = args.get(key)
                if isinstance(value, int) and not isinstance(value, bool):
                    summary[key] = value
        else:
            for key in ('old_text', 'new_text') if tool_name == 'workspace_edit' else ('content',):
                content = args.get(key)
                if isinstance(content, str):
                    summary[f'{key}_bytes'] = len(content.encode('utf-8', errors='replace'))
            summary["has_expected_sha256"] = isinstance(args.get("expected_sha256"), str)
        return json.dumps(summary, ensure_ascii=False, separators=(",", ":"))
    summary: dict[str, int | str] = {}
    for key in _TOOL_ARG_ALLOWLIST.get(tool_name, ()):
        value = args.get(key)
        if key in {"artifact_id", "expected_version"} and isinstance(value, int) and not isinstance(value, bool):
            summary[key] = value
        elif key in {"kind", "language"} and isinstance(value, str) and _SAFE_LABEL.fullmatch(value):
            summary[key] = value
    return json.dumps(summary, ensure_ascii=False, separators=(",", ":"))


def summarize_tool_output(output: Any) -> str:
    """只记录输出形态和大小，不序列化工具返回的正文或嵌套值。

    Args:
        output：工具执行层返回的原始对象。
    """
    if isinstance(output, str):
        summary = {"type": "text", "chars": len(output)}
    elif isinstance(output, (bytes, bytearray)):
        summary = {"type": "bytes", "bytes": len(output)}
    elif isinstance(output, (list, tuple, dict, set)):
        summary = {"type": type(output).__name__, "items": len(output)}
    elif output is None:
        summary = {"type": "none"}
    else:
        summary = {"type": type(output).__name__}
    return json.dumps(summary, ensure_ascii=False, separators=(",", ":"))


def command_result_summary(tool_name: str, output: Any) -> dict[str, Any]:
    """从命令或原生文件结果提取公开允许字段，不复制任何输出原文。

    Args:
        tool_name：防腐层提供的工具名称。
        output：工具结果文本或 ToolMessage。
    """
    if tool_name not in {'workspace_run_command', 'workspace_run_shell', 'workspace_edit', 'workspace_read'}:
        return {}
    content = getattr(output, 'content', output)
    if not isinstance(content, str):
        return {}
    for prefix in (FAILED_OUTPUT_PREFIX, REJECTED_OUTPUT_PREFIX):
        if content.startswith(prefix):
            content = content[len(prefix):].strip()
            break
    try:
        result = json.loads(content)
    except (ValueError, TypeError):
        return {}
    if not isinstance(result, dict):
        return {}
    if tool_name == 'workspace_read':
        code = result.get('error_code')
        allowed = {'WORKSPACE_BATCH_ARGUMENT_INVALID', 'WORKSPACE_BATCH_INPUT_TOO_LARGE', 'WORKSPACE_BATCH_BUSY',
            'WORKSPACE_BATCH_PARTIAL', 'WORKSPACE_BATCH_FAILED', 'WORKSPACE_TOOL_NOT_AVAILABLE', 'WORKSPACE_READ_ARGUMENT_INVALID'}
        return {'error_code': code} if isinstance(code, str) and code in allowed else {}
    if tool_name == 'workspace_edit':
        allowed = {'WORKSPACE_EDIT_ARGUMENT_INVALID', 'WORKSPACE_EDIT_INPUT_TOO_LARGE',
            'WORKSPACE_EDIT_MATCH_NOT_FOUND', 'WORKSPACE_EDIT_MATCH_AMBIGUOUS', 'WORKSPACE_FILE_REVISION_CONFLICT',
            'WORKSPACE_FILE_NOT_FOUND', 'WORKSPACE_FILE_NOT_TEXT', 'WORKSPACE_FILE_TOO_LARGE',
            'WORKSPACE_PATH_INVALID', 'WORKSPACE_PATH_OUTSIDE_ROOT', 'WORKSPACE_PATH_SENSITIVE', 'WORKSPACE_TOOL_NOT_AVAILABLE'}
        code = result.get('error_code')
        return {'error_code': code} if isinstance(code, str) and code in allowed else {}
    summary: dict[str, Any] = {}
    if result.get('command') in ('pwd', 'list', 'read', 'count'):
        summary['command'] = result['command']
    if result.get('status') in ('exited', 'timed_out', 'cancelled'):
        summary['command_status'] = result['status']
    exit_code = result.get('exit_code')
    if exit_code is None or type(exit_code) is int:
        summary['exit_code'] = exit_code
    if type(result.get('truncated')) is bool:
        summary['truncated'] = result['truncated']
    from ..workspaces.command_worker import WORKER_ERRORS
    if result.get('error_code') in WORKER_ERRORS | {'COMMAND_TIMEOUT', 'COMMAND_NOT_SUPPORTED',
        'SHELL_REJECTED', 'SHELL_APPROVAL_EXPIRED', 'SHELL_APPROVAL_MISMATCH', 'SHELL_ARGUMENT_INVALID',
        'SHELL_NOT_SUPPORTED', 'SHELL_REQUEST_CONFLICT', 'WORKSPACE_TOOL_NOT_AVAILABLE',
        'RUNTIME_WORLD_LIMIT', 'RUNTIME_WORKSPACE_LIMIT', 'RUNTIME_CONVERSATION_LIMIT', 'RUNTIME_SCOPE_CLOSING'}:
        summary['error_code'] = result['error_code']
    return summary
