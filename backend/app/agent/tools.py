"""工具危险分级与执行层拦截。

分级默认封闭：只有显式白名单里的内置工具是 safe，MCP 工具、有副作用的内置工具
和任何未知工具一律 dangerous。分级判断和拦截都发生在工具执行层，不依赖系统提示词、
工具名自述或模型的配合；被拒绝的调用以结构化结果回传模型，让本轮仍能正常收尾，
而不是抛异常中断整条链。
"""
from __future__ import annotations

from collections.abc import Collection, Sequence
from typing import Any

from langchain_core.tools import BaseTool

# 显式 safe 白名单：只包含只读或纯生成类内置工具。
# 新增工具默认不在此列表内，必须经安全评审后显式加入。
SAFE_BUILTIN_TOOLS: frozenset[str] = frozenset({"create_artifact", "update_artifact", "read_artifact"})

# MCP 工具统一使用该前缀，便于审计与排查；前缀本身不授予任何权限。
MCP_TOOL_PREFIX = "mcp_"

# 被拒绝的工具结果以该前缀开头，防腐层据此把结束事件标记为 rejected。
REJECTED_OUTPUT_PREFIX = "[工具被拒绝]"

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

    async def _arun(self, *args: Any, **kwargs: Any) -> Any:
        if self.danger == DANGER_DANGEROUS and not self.allow_dangerous:
            return self._reject()
        return await self.inner.arun(kwargs or (args[0] if args else {}))


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
            )
        )
    return guarded


def summarize_args(args: Any, *, limit: int = 200) -> str:
    """把工具参数压缩为可写入审计的摘要。

    审计需要能回答"调了什么工具、大致参数是什么"，但不得保存凭据或敏感原文，
    因此这里只做截断而不做结构化保存；调用方仍需保证不把凭据放进工具参数。
    """
    text = str(args)
    return text if len(text) <= limit else f"{text[:limit]}…"
