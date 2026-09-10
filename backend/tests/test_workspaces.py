"""W1a 当前 World 工作区、会话绑定与原生文件工具行为测试。"""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from accounts import TEST_PASSWORD, ensure_owner_async, guest_username


def _headers(token_body: dict) -> dict[str, str]:
    """把认证响应转换为 Bearer 请求头。"""
    return {"Authorization": f"Bearer {token_body['access_token']}"}


async def _wait_for_role_message(
    client: AsyncClient, headers: dict[str, str], conversation_id: int,
) -> list[dict]:
    """等待 fake Agent 工具循环到达终态。"""
    for _ in range(200):
        await asyncio.sleep(0.05)
        response = await client.get(f"/api/conversations/{conversation_id}/messages", headers=headers)
        items = response.json()["items"]
        if any(item["sender_type"] == "role" and item["status"] in {"done", "error"} for item in items):
            return items
    raise AssertionError("工作区工具生成未在预期时间内完成")


@pytest.mark.anyio
async def test_owner_can_register_multiple_absolute_workspace_roots_and_guest_cannot_list(
    tmp_path: Path,
):
    """同一 World Owner 可登记多个不同绝对根；Guest 不能枚举路径。"""
    from app.config import settings
    from app.main import app

    existing = tmp_path / "round-alpha"
    another = tmp_path / "another-root"
    existing.mkdir()
    another.mkdir()
    (existing / "seed.txt").write_text("seed", encoding="utf-8")

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            owner = await ensure_owner_async(client)
            owner_headers = _headers(owner)

            capabilities = await client.get("/api/workspaces/capabilities", headers=owner_headers)
            assert capabilities.status_code == 200, capabilities.text
            base_capabilities = {
                "world_name": settings.world_name,
                "workspace_kinds": ["managed_directory"],
                "file_tools": ["workspace_list", "workspace_read", "workspace_write"],
                "basic_commands_available": True,
            }
            assert {key: capabilities.json()[key] for key in base_capabilities} == base_capabilities
            assert capabilities.json()['shell_available'] == (capabilities.json()['shell_kind'] is not None)
            assert capabilities.json()['shell_approval_mode'] == 'per_call'
            assert capabilities.json()['shell_timeout_seconds'] == settings.workspace_command_timeout_seconds
            assert capabilities.json()['shell_output_bytes'] == settings.workspace_command_output_bytes
            no_ack = await client.post("/api/workspaces", headers=owner_headers, json={
                "display_name": "Alpha 工作区",
                "root_path": str(existing),
                "create_directory": False,
                "acknowledge_existing_content": False,
            })
            assert no_ack.status_code == 422
            assert no_ack.json()["error"]["code"] == "WORKSPACE_EXISTING_CONTENT_ACK_REQUIRED"

            created = await client.post("/api/workspaces", headers=owner_headers, json={
                "display_name": "Alpha 工作区",
                "root_path": str(existing),
                "create_directory": False,
                "acknowledge_existing_content": True,
            })
            assert created.status_code == 201, created.text
            workspace = created.json()
            assert workspace["root_path"] == str(existing.resolve())
            assert workspace["availability"] == "available"
            assert workspace["file_tools_enabled"] is False

            second = await client.post("/api/workspaces", headers=owner_headers, json={
                "display_name": "另一个根",
                "root_path": str(another),
                "create_directory": False,
                "acknowledge_existing_content": True,
            })
            assert second.status_code == 201, second.text
            listed = await client.get("/api/workspaces", headers=owner_headers)
            assert [item["root_path"] for item in listed.json()][-2:] == [
                str(existing.resolve()), str(another.resolve()),
            ]

            enabled = await client.patch(
                f"/api/workspaces/{workspace['id']}", headers=owner_headers,
                json={"file_tools_enabled": True},
            )
            assert enabled.status_code == 200, enabled.text
            assert enabled.json()["file_tools_enabled"] is True

            guest = await client.post("/api/auth/register", json={
                "username": guest_username("workspace-list"),
                "password": TEST_PASSWORD,
                "nickname": "工作区 Guest",
            })
            assert guest.status_code == 201
            denied = await client.get("/api/workspaces", headers=_headers(guest.json()))
            assert denied.status_code == 403
            assert denied.json()["error"]["code"] == "OWNER_REQUIRED"


@pytest.mark.anyio
async def test_workspace_creation_rejects_escape_and_never_deletes_physical_directory(
    tmp_path: Path,
):
    """根必须是绝对路径；创建只触及精确目标，解除登记不删除宿主内容。"""
    from app.main import app

    parent = tmp_path / "parent"
    parent.mkdir()

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            owner = await ensure_owner_async(client)
            headers = _headers(owner)
            escaped = await client.post("/api/workspaces", headers=headers, json={
                "display_name": "相对路径",
                "root_path": "../Roleplex",
                "create_directory": True,
            })
            assert escaped.status_code == 422
            assert escaped.json()["error"]["code"] == "WORKSPACE_ROOT_PATH_INVALID"

            target = parent / "round-created"
            created = await client.post("/api/workspaces", headers=headers, json={
                "display_name": "新建目录",
                "root_path": str(target),
                "create_directory": True,
            })
            assert created.status_code == 201, created.text
            assert created.json()["root_path"] == str(target.resolve())
            assert target.is_dir() and not any(target.iterdir())
            marker = target / "keep.txt"
            marker.write_text("do not delete", encoding="utf-8")

            removed = await client.delete(f"/api/workspaces/{created.json()['id']}", headers=headers)
            assert removed.status_code == 204, removed.text
            assert marker.read_text(encoding="utf-8") == "do not delete"


@pytest.mark.anyio
async def test_only_single_conversation_can_bind_an_available_workspace(
    tmp_path: Path,
):
    """会话绑定使用 revision 乐观锁，群聊不能提交 W1a 工作区。"""
    from app.main import app

    workspace_root = tmp_path / "single"
    workspace_root.mkdir()

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            owner = await ensure_owner_async(client)
            headers = _headers(owner)
            config = await client.post("/api/model-configs", headers=headers, json={
                "name": "workspace-config", "provider_type": "openai_compatible", "api_key": "sk-placeholder",
            })
            role_ids: list[int] = []
            for index in range(2):
                role = await client.post("/api/roles", headers=headers, json={
                    "name": f"workspace-role-{index}", "system_prompt": "按要求调用工作区工具",
                    "model_config_id": config.json()["id"], "model_name": "fake-model",
                    "builtin_tools": ["workspace_list", "workspace_read", "workspace_write"],
                })
                assert role.status_code == 201, role.text
                role_ids.append(role.json()["id"])
            workspace = await client.post("/api/workspaces", headers=headers, json={
                "display_name": "绑定目标", "root_path": str(workspace_root),
                "create_directory": False, "acknowledge_existing_content": True,
            })
            workspace_id = workspace.json()["id"]
            await client.patch(
                f"/api/workspaces/{workspace_id}", headers=headers, json={"file_tools_enabled": True},
            )

            single = await client.post("/api/conversations", headers=headers, json={
                "type": "single", "title": "workspace single", "role_ids": role_ids[:1],
            })
            bound = await client.put(
                f"/api/conversations/{single.json()['id']}/workspace", headers=headers,
                json={"workspace_binding_id": workspace_id, "expected_revision": 0},
            )
            assert bound.status_code == 200, bound.text
            assert bound.json()["workspace_binding_id"] == workspace_id
            assert bound.json()["revision"] == 1
            stale = await client.put(
                f"/api/conversations/{single.json()['id']}/workspace", headers=headers,
                json={"workspace_binding_id": None, "expected_revision": 0},
            )
            assert stale.status_code == 409
            assert stale.json()["error"]["code"] == "CONVERSATION_REVISION_CONFLICT"

            group = await client.post("/api/conversations", headers=headers, json={
                "type": "group", "title": "workspace group", "role_ids": role_ids,
            })
            rejected = await client.put(
                f"/api/conversations/{group.json()['id']}/workspace", headers=headers,
                json={"workspace_binding_id": workspace_id, "expected_revision": 0},
            )
            assert rejected.status_code == 422
            assert rejected.json()["error"]["code"] == "SINGLE_CHAT_REQUIRED"


@pytest.mark.anyio
async def test_native_file_service_enforces_utf8_hash_and_concurrent_revision(
    tmp_path: Path,
):
    """原生文件服务不拆坏 UTF-8，且同一旧 hash 的并发更新最多一个成功。"""
    from app.workspaces.files import WorkspaceFileError, WorkspaceFileService

    root = tmp_path / "workspace"
    root.mkdir()
    service = WorkspaceFileService(root=root, execution_id="exec-test")

    created = await service.write("hello.txt", "你好，workspace", expected_sha256=None)
    assert created.created is True
    initial_hash = hashlib.sha256("你好，workspace".encode()).hexdigest()
    assert created.sha256 == initial_hash

    first = await service.read("hello.txt", offset_bytes=0, max_bytes=4)
    assert first.text == "你"
    assert first.next_offset == len("你".encode())
    assert first.eof is False
    remainder = await service.read("hello.txt", offset_bytes=first.next_offset, max_bytes=65536)
    assert first.text + remainder.text == "你好，workspace"
    assert remainder.sha256 == initial_hash
    tiny = await service.read("hello.txt", offset_bytes=0, max_bytes=1)
    assert tiny.text == "你" and tiny.next_offset == len("你".encode())
    await service.write("empty.txt", "", expected_sha256=None)
    empty = await service.read("empty.txt")
    assert empty.text == "" and empty.bytes == 0 and empty.eof is True

    async def update(content: str):
        try:
            return await service.write("hello.txt", content, expected_sha256=initial_hash)
        except WorkspaceFileError as exc:
            return exc.code

    results = await asyncio.gather(update("版本 A"), update("版本 B"))
    assert sum(not isinstance(result, str) for result in results) == 1
    assert results.count("WORKSPACE_FILE_REVISION_CONFLICT") == 1

    with pytest.raises(WorkspaceFileError, match="WORKSPACE_FILE_REVISION_CONFLICT"):
        await service.write("hello.txt", "无 hash 覆盖", expected_sha256=None)
    with pytest.raises(WorkspaceFileError) as escaped:
        await service.read("../Roleplex/README.md")
    assert escaped.value.code in {"WORKSPACE_PATH_INVALID", "WORKSPACE_PATH_OUTSIDE_ROOT"}


@pytest.mark.anyio
async def test_native_file_service_reports_symlinks_without_following_them(tmp_path: Path):
    """目录列表可标记 symlink，但 read/write 永远不能沿链接访问根外文件。"""
    from app.workspaces.files import WorkspaceFileError, WorkspaceFileService

    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = root / "outside-link"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("当前平台不允许创建测试符号链接")
    service = WorkspaceFileService(root=root, execution_id="exec-symlink")
    (root / ".env").write_text("SECRET=never-send", encoding="utf-8")
    (root / "binary.bin").write_bytes(b"\x00\xff")

    listing = await service.list(".")
    assert listing.items == [
        {"name": "binary.bin", "type": "file", "size": 2},
        {"name": "outside-link", "type": "symlink", "size": None},
    ]
    assert ".env" not in str(listing.items)
    with pytest.raises(WorkspaceFileError) as sensitive:
        await service.read(".env")
    assert sensitive.value.code == "WORKSPACE_PATH_SENSITIVE"
    with pytest.raises(WorkspaceFileError) as binary:
        await service.read("binary.bin")
    assert binary.value.code == "WORKSPACE_FILE_NOT_TEXT"
    with pytest.raises(WorkspaceFileError) as blocked:
        await service.read("outside-link")
    assert blocked.value.code == "WORKSPACE_PATH_OUTSIDE_ROOT"


@pytest.mark.anyio
async def test_fake_agent_uses_bound_workspace_tools_and_retains_execution_lease(
    tmp_path: Path,
):
    """fake Agent 必须走真实工具循环完成写读，终态 lease 变 retained。"""
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.main import app
    from app.models import ExecutionWorkspace, ToolCall

    workspace_root = tmp_path / "agent-round"
    workspace_root.mkdir()

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            owner = await ensure_owner_async(client)
            headers = _headers(owner)
            config = await client.post("/api/model-configs", headers=headers, json={
                "name": "workspace-agent-config", "provider_type": "openai_compatible", "api_key": "sk-placeholder",
            })
            role = await client.post("/api/roles", headers=headers, json={
                "name": "workspace-agent", "system_prompt": "执行 W1a 文件工具验收",
                "model_config_id": config.json()["id"], "model_name": "fake-model",
                "builtin_tools": ["workspace_list", "workspace_read", "workspace_write"],
            })
            workspace = await client.post("/api/workspaces", headers=headers, json={
                "display_name": "Agent 工作区", "root_path": str(workspace_root),
                "create_directory": False, "acknowledge_existing_content": True,
            })
            workspace_id = workspace.json()["id"]
            await client.patch(
                f"/api/workspaces/{workspace_id}", headers=headers, json={"file_tools_enabled": True},
            )
            conversation = await client.post("/api/conversations", headers=headers, json={
                "type": "single", "title": "W1a fake 工具闭环", "role_ids": [role.json()["id"]],
                "workspace_binding_id": workspace_id,
            })
            sent = await client.post(
                f"/api/conversations/{conversation.json()['id']}/messages", headers=headers,
                json={"parts": [{"type": "text", "text": "[W1A_FAKE_E2E] 完成 hello.txt 写读更新验收"}]},
            )
            assert sent.status_code == 202, sent.text
            messages = await _wait_for_role_message(client, headers, conversation.json()["id"])

            assert (workspace_root / "hello.txt").read_text(encoding="utf-8") == "W1a 第二版"
            assistant = next(item for item in messages if item["sender_type"] == "role")
            assert "W1a 工作区工具闭环完成" in "".join(
                part.get("text", "") for part in assistant["parts_json"] if part["type"] == "text"
            )
            async with SessionLocal() as session:
                calls = (await session.scalars(select(ToolCall).where(
                    ToolCall.conversation_id == conversation.json()["id"],
                ).order_by(ToolCall.id))).all()
                leases = (await session.scalars(select(ExecutionWorkspace).where(
                    ExecutionWorkspace.workspace_binding_id == workspace_id,
                ))).all()

    assert [call.tool_name for call in calls] == [
        "workspace_list", "workspace_write", "workspace_read", "workspace_write", "workspace_read",
    ]
    assert all(call.execution_id and call.workspace_binding_id == workspace_id for call in calls)
    assert all("hello.txt" not in call.args_summary and "W1a" not in call.args_summary for call in calls)
    assert len(leases) == 1 and leases[0].status == "retained" and leases[0].ended_at is not None
