from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator


async def stream_fake_reply(prompt: str, *, delay: float = 0.08) -> AsyncIterator[str]:
    """按固定规则输出可复现的文本片段，供普通回归测试使用。

    Args:
        prompt：用户当前消息文本。
        delay：相邻片段之间的固定延迟秒数。
    """
    text = f"已收到你的消息：{prompt}\n\n这是 M2 fake provider 的确定性回复。"
    for index in range(0, len(text), 6):
        await asyncio.sleep(delay)
        yield text[index:index + 6]
