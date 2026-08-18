"""产物原始内容读取的隔离与授权测试。

验证三条安全边界：内容安全策略与嗅探保护始终存在、只有作为 iframe 子资源时才按类型
渲染（顶层打开被强制下载）、以及非会话成员完全看不到产物。
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from accounts import TEST_PASSWORD, ensure_owner_async, guest_username

HTML_CONTENT = "<html><body><h1>产物预览</h1><script>console.log('hi')</script></body></html>"


async def _seed_artifact(client: AsyncClient, headers: dict[str, str], tag: str) -> tuple[int, int]:
    """创建一个会话并直接写入产物与版本，返回 `(会话 ID, 产物 ID)`。

    产物创建接口属于后续里程碑，这里用 ORM 造数据，只验证读取端点的隔离行为。

    Args:
        client：已认证的测试客户端。
        headers：Owner 认证头。
        tag：用例标识，用于生成互不冲突的角色名（角色名在 Owner 范围内唯一）。
    """
    from app.db import SessionLocal, now_utc
    from app.models import Artifact, ArtifactVersion

    config = await client.post("/api/model-configs", headers=headers, json={
        "name": f"cfg-artifact-{tag}", "provider_type": "openai_compatible", "api_key": "sk-test-placeholder",
    })
    role = await client.post("/api/roles", headers=headers, json={
        "name": f"角色-产物-{tag}", "system_prompt": "你是测试助手",
        "model_config_id": config.json()["id"], "model_name": "fake-model",
    })
    conversation = await client.post("/api/conversations", headers=headers, json={
        "type": "single", "title": f"产物-{tag}", "role_ids": [role.json()["id"]],
    })
    conversation_id = conversation.json()["id"]

    async with SessionLocal() as session:
        artifact = Artifact(conversation_id=conversation_id, kind="html", title="预览页", current_version=1)
        session.add(artifact)
        await session.flush()
        session.add(ArtifactVersion(
            artifact_id=artifact.id, version=1, content=HTML_CONTENT, created_at=now_utc(),
        ))
        await session.commit()
        return conversation_id, artifact.id


@pytest.mark.anyio
async def test_iframe_request_is_rendered_with_isolation_headers():
    """作为 iframe 子资源加载时按类型渲染，并且必须带完整隔离响应头。"""
    from app.main import app
    from app.db import engine
    from app.routers.artifacts import CONTENT_SECURITY_POLICY

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = {"Authorization": f"Bearer {(await ensure_owner_async(client))['access_token']}"}
            _, artifact_id = await _seed_artifact(client, headers, "iframe")

            response = await client.get(
                f"/api/artifacts/{artifact_id}/versions/1/raw",
                headers={**headers, "Sec-Fetch-Dest": "iframe"},
            )

    assert response.status_code == 200
    assert response.text == HTML_CONTENT
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    assert "content-disposition" not in response.headers
    await engine.dispose()


@pytest.mark.anyio
async def test_top_level_open_is_forced_to_download():
    """顶层直接打开必须变成下载，避免产物脚本拿到本站同源上下文。"""
    from app.main import app
    from app.db import engine

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = {"Authorization": f"Bearer {(await ensure_owner_async(client))['access_token']}"}
            _, artifact_id = await _seed_artifact(client, headers, "download")

            top_level = await client.get(
                f"/api/artifacts/{artifact_id}/versions/1/raw",
                headers={**headers, "Sec-Fetch-Dest": "document"},
            )
            # 不发送该请求头的客户端同样按最保守方式处理。
            headless = await client.get(f"/api/artifacts/{artifact_id}/versions/1/raw", headers=headers)

    for response in (top_level, headless):
        assert response.status_code == 200
        assert response.headers["content-disposition"].startswith("attachment;")
        assert response.headers["content-security-policy"]
    await engine.dispose()


@pytest.mark.anyio
async def test_non_member_and_anonymous_cannot_read_artifact():
    """非会话成员按不存在处理，未认证请求返回 401。"""
    from app.main import app
    from app.db import engine

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = {"Authorization": f"Bearer {(await ensure_owner_async(client))['access_token']}"}
            _, artifact_id = await _seed_artifact(client, headers, "authz")

            guest = await client.post("/api/auth/register", json={
                "username": guest_username("artifact"), "password": TEST_PASSWORD, "nickname": "Guest",
            })
            guest_headers = {"Authorization": f"Bearer {guest.json()['access_token']}"}

            hidden = await client.get(f"/api/artifacts/{artifact_id}/versions/1/raw", headers=guest_headers)
            anonymous = await client.get(f"/api/artifacts/{artifact_id}/versions/1/raw")
            missing_version = await client.get(f"/api/artifacts/{artifact_id}/versions/9/raw", headers=headers)

    assert hidden.status_code == 404 and hidden.json()["error"]["code"] == "ARTIFACT_NOT_FOUND"
    assert anonymous.status_code == 401
    # 版本不存在与无权访问返回同一个错误码，不泄露产物是否存在。
    assert missing_version.status_code == 404 and missing_version.json()["error"]["code"] == "ARTIFACT_NOT_FOUND"
    await engine.dispose()
