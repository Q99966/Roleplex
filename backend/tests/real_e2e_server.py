"""真实浏览器端到端测试的安全播种与后端启动入口。

该入口只由 ``frontend/playwright.real.config.ts`` 显式调用。它在启动 uvicorn 前创建
独立测试库中的 Owner、加密模型配置、角色与单聊会话；真实 API Key 只从本地 ``.env``
进入后端加密边界，不传给浏览器、不打印、不写入 Playwright trace。
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

OWNER_PASSWORD = "Roleplex-Real-E2E-1"


def _load_env_file() -> None:
    """从本地 ``backend/.env`` 补齐真实厂商配置，且不覆盖显式环境变量。"""
    env_path = BACKEND_DIR / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _required_setting(name: str) -> str:
    """读取必需环境变量；缺失时只报告变量名，不泄露其他配置。"""
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"真实 E2E 缺少环境变量：{name}")
    return value


async def seed_database() -> None:
    """为本轮真实 E2E 创建可登录且能调用真实模型的最小单聊场景。"""
    from sqlalchemy import select

    from app.db import SessionLocal, close_db, init_db
    from app.models import Conversation, ConversationMember, InstanceSettings, ModelConfig, Role, User
    from app.security import encrypt_api_key, hash_password

    stamp = _required_setting("ROLEPLEX_REAL_E2E_STAMP")
    api_key = _required_setting("ROLEPLEX_CONTRACT_OPENAI_KEY")
    model_name = _required_setting("ROLEPLEX_CONTRACT_OPENAI_MODEL")
    base_url = os.environ.get("ROLEPLEX_CONTRACT_OPENAI_BASE_URL", "").strip() or None
    username = f"realtest{stamp}"
    now = datetime.now(timezone.utc)

    await init_db()
    try:
        async with SessionLocal() as session:
            instance = await session.get(InstanceSettings, 1)
            if instance is None:
                raise RuntimeError("真实 E2E 初始化失败：Owner 单例不存在")

            owner = await session.scalar(select(User).where(User.username == username))
            if owner is None:
                if instance.owner_user_id is not None:
                    raise RuntimeError("真实 E2E 数据库已被其他 Owner 认领，请使用新的时间戳")
                owner = User(
                    username=username,
                    password_hash=hash_password(OWNER_PASSWORD),
                    nickname=f"真实 E2E Owner {stamp}",
                    is_owner=True,
                    token_version=0,
                    created_at=now,
                )
                session.add(owner)
                await session.flush()
                instance.owner_user_id = owner.id
            elif not owner.is_owner or instance.owner_user_id != owner.id:
                raise RuntimeError("真实 E2E Owner 状态不一致，请使用新的时间戳")

            config_name = f"真实 E2E 模型 {stamp}"
            model_config = await session.scalar(select(ModelConfig).where(
                ModelConfig.created_by == owner.id,
                ModelConfig.name == config_name,
            ))
            if model_config is None:
                model_config = ModelConfig(
                    created_by=owner.id,
                    name=config_name,
                    provider_type="openai_compatible",
                    base_url=base_url,
                    api_key_encrypted=encrypt_api_key(api_key),
                    capability_overrides_json={},
                    created_at=now,
                )
                session.add(model_config)
            else:
                model_config.base_url = base_url
                model_config.api_key_encrypted = encrypt_api_key(api_key)
            await session.flush()

            role_name = f"真实 E2E 助手 {stamp}"
            role = await session.scalar(select(Role).where(
                Role.created_by == owner.id,
                Role.name == role_name,
                Role.deleted_at.is_(None),
            ))
            if role is None:
                role = Role(
                    created_by=owner.id,
                    name=role_name,
                    description="调用真实厂商的浏览器端到端测试角色",
                    tags_json=["真实 API", "E2E"],
                    system_prompt="你是 Roleplex 真实 API 测试助手，请用一句中文简短回复。",
                    model_config_id=model_config.id,
                    model_name=model_name,
                    params_json={"temperature": 0.0, "max_tokens": 128},
                    skills_json=[],
                    builtin_tools_json=[],
                    mcp_servers_json=[],
                    mcp_tools_cache_json=[],
                    active=True,
                    created_at=now,
                    updated_at=now,
                )
                session.add(role)
            else:
                role.model_config_id = model_config.id
                role.model_name = model_name
                role.active = True
                role.updated_at = now
            await session.flush()

            # M4a 真实群聊使用两个独立角色：协作码只写入 A 的 system prompt，B 必须从
            # A 已提交的群聊回复中读取，才能在自己的后续回合复述出来。
            collaboration_code = f"RG-{stamp[-6:]}"
            group_roles: list[Role] = []
            for marker in ("A", "B"):
                group_role_name = f"真实群聊角色 {marker} {stamp}"
                group_role = await session.scalar(select(Role).where(
                    Role.created_by == owner.id,
                    Role.name == group_role_name,
                    Role.deleted_at.is_(None),
                ))
                group_prompt = (
                    f"你是群聊角色 A。你的内部协作码是 {collaboration_code}；被要求时只回复该协作码。"
                    if marker == "A"
                    else "你是群聊角色 B。请读取本轮前一个 Agent 已提交的回复，并只复述其中的协作码；不要猜测。"
                )
                if group_role is None:
                    group_role = Role(
                        created_by=owner.id,
                        name=group_role_name,
                        description=f"M4a 真实串行群聊角色 {marker}",
                        tags_json=["真实 API", "M4a"],
                        system_prompt=group_prompt,
                        model_config_id=model_config.id,
                        model_name=model_name,
                        params_json={"temperature": 0.0, "max_tokens": 64},
                        skills_json=[],
                        builtin_tools_json=[],
                        mcp_servers_json=[],
                        mcp_tools_cache_json=[],
                        active=True,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(group_role)
                else:
                    group_role.system_prompt = group_prompt
                    group_role.model_config_id = model_config.id
                    group_role.model_name = model_name
                    group_role.active = True
                    group_role.updated_at = now
                await session.flush()
                group_roles.append(group_role)

            group_title = f"真实群聊验证 {stamp}"
            group_conversation = await session.scalar(select(Conversation).where(
                Conversation.created_by == owner.id,
                Conversation.title == group_title,
            ))
            if group_conversation is None:
                group_conversation = Conversation(
                    type="group",
                    title=group_title,
                    orchestrator_enabled=False,
                    created_by=owner.id,
                    revision=0,
                    event_seq=0,
                    created_at=now,
                )
                session.add(group_conversation)
                await session.flush()
                session.add(ConversationMember(
                    conversation_id=group_conversation.id,
                    member_type="user",
                    member_id=owner.id,
                    pinned=False,
                    archived=False,
                    joined_at=now,
                ))
                for group_role in group_roles:
                    session.add(ConversationMember(
                        conversation_id=group_conversation.id,
                        member_type="role",
                        member_id=group_role.id,
                        pinned=False,
                        archived=False,
                        joined_at=now,
                    ))

            title = f"真实 API 验证 {stamp}"
            conversation = await session.scalar(select(Conversation).where(
                Conversation.created_by == owner.id,
                Conversation.title == title,
            ))
            if conversation is None:
                conversation = Conversation(
                    type="single",
                    title=title,
                    orchestrator_enabled=False,
                    created_by=owner.id,
                    revision=0,
                    event_seq=0,
                    created_at=now,
                )
                session.add(conversation)
                await session.flush()
                session.add_all([
                    ConversationMember(
                        conversation_id=conversation.id,
                        member_type="user",
                        member_id=owner.id,
                        pinned=False,
                        archived=False,
                        joined_at=now,
                    ),
                    ConversationMember(
                        conversation_id=conversation.id,
                        member_type="role",
                        member_id=role.id,
                        pinned=False,
                        archived=False,
                        joined_at=now,
                    ),
                ])
            await session.commit()
    finally:
        # uvicorn 会建立自己的事件循环；先释放播种阶段的连接，避免跨循环复用。
        await close_db()

    print(f"真实 E2E 数据已就绪：账号 {username}，模型 {model_name}")


def main() -> int:
    """播种本轮数据库并以前台进程启动真实 provider 后端。"""
    _load_env_file()
    try:
        asyncio.run(seed_database())
    except Exception as exc:
        # 异常消息只包含稳定说明或变量名；不得附带环境变量值。
        print(f"真实 E2E 后端启动失败：{exc}", file=sys.stderr)
        return 1

    import uvicorn

    port = int(os.environ.get("ROLEPLEX_REAL_E2E_API_PORT", "8002"))
    uvicorn.run("app.main:app", host="127.0.0.1", port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
