"""产物原始内容读取。

产物可能包含模型生成的 HTML 与脚本，直接同源打开等于让它在本站上下文里执行，
可以读走浏览器里的登录凭据。因此这里固定三重隔离：内容安全策略禁止一切外部资源与
网络请求、禁止浏览器嗅探类型、并且只允许作为 iframe 子资源加载——顶层直接打开时
强制变成下载。
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import Artifact, ArtifactVersion, ConversationMember, User
from ..security.tokens import get_current_user

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])

# 只允许内联脚本与样式渲染自身，禁止任何外部资源、网络请求和被第三方站点嵌套。
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src data: blob:; connect-src 'none'; frame-ancestors 'self'"
)

# 产物类型到响应类型与下载扩展名的映射；未知类型按纯文本处理，绝不回退成 HTML。
_CONTENT_TYPES: dict[str, tuple[str, str]] = {
    "html": ("text/html; charset=utf-8", "html"),
    "svg": ("image/svg+xml; charset=utf-8", "svg"),
    "markdown": ("text/plain; charset=utf-8", "md"),
    "code": ("text/plain; charset=utf-8", "txt"),
}


async def _load_version(
    session: AsyncSession, artifact_id: int, version: int, user_id: int
) -> tuple[Artifact, ArtifactVersion]:
    """按资源级授权加载指定版本的产物内容。

    Args:
        session：数据库会话。
        artifact_id：产物标识。
        version：版本号，历史消息引用固定版本。
        user_id：请求者。

    Returns:
        `(产物, 版本)` 元组。

    Raises:
        HTTPException：产物不存在、版本不存在或请求者不是所属会话成员时一律返回
            404 且错误码相同，不泄露资源是否存在。
    """
    artifact = await session.get(Artifact, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="ARTIFACT_NOT_FOUND")
    member = await session.scalar(select(ConversationMember).where(
        ConversationMember.conversation_id == artifact.conversation_id,
        ConversationMember.member_type == "user",
        ConversationMember.member_id == user_id,
    ))
    if member is None:
        raise HTTPException(status_code=404, detail="ARTIFACT_NOT_FOUND")
    row = await session.scalar(select(ArtifactVersion).where(
        ArtifactVersion.artifact_id == artifact_id, ArtifactVersion.version == version,
    ))
    if row is None:
        raise HTTPException(status_code=404, detail="ARTIFACT_NOT_FOUND")
    return artifact, row


@router.get("/{artifact_id}/versions/{version}/raw")
async def read_raw(
    artifact_id: int,
    version: int,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """返回产物某个版本的原始内容，供加固后的 iframe 预览使用。

    浏览器在子资源请求上会带 `Sec-Fetch-Dest`。只有它明确是 `iframe` 时才按内容类型
    渲染；其余情况（顶层地址栏直接打开、脚本抓取、以及不发送该头的客户端）一律作为
    附件下载，避免产物脚本获得本站同源上下文。
    """
    artifact, row = await _load_version(session, artifact_id, version, user.id)
    content_type, extension = _CONTENT_TYPES.get(artifact.kind, ("text/plain; charset=utf-8", "txt"))

    headers = {
        "Content-Security-Policy": CONTENT_SECURITY_POLICY,
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
    }
    if request.headers.get("Sec-Fetch-Dest") != "iframe":
        headers["Content-Disposition"] = f'attachment; filename="artifact-{artifact_id}-v{version}.{extension}"'
    return Response(content=row.content, media_type=content_type, headers=headers)
