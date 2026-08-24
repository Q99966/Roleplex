"""开发用：把本地 .env 中的厂商凭据写成一份可用的模型配置与角色。

用途是手动跑真实聊天链路（浏览器或 API），避免每次都在界面里重新填一遍 Key。
只写入本地开发数据库，凭据按产品同一条路径加密落库，脚本不打印任何 Key。

用法（在 backend 目录，需先在 .env 或环境变量里配置凭据）：
    python scripts/seed_dev_provider.py

正常启动默认走真实厂商；仅在显式设置 `AGENT_USE_FAKE_PROVIDER=true` 时使用假回应。
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

CONFIG_NAME = "DeepSeek（开发）"
ROLE_NAME = "DeepSeek 助手"


def _load_env_file() -> None:
    """把 .env 中缺失的键补进环境变量；已显式设置的环境变量优先。"""
    env_path = BACKEND_DIR / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


async def seed() -> int:
    """创建或更新开发用模型配置与角色，返回进程退出码。"""
    from sqlalchemy import select

    from app.db import SessionLocal, init_db
    from app.models import InstanceSettings, ModelConfig, Role
    from app.security import encrypt_api_key

    api_key = os.environ.get("ROLEPLEX_CONTRACT_OPENAI_KEY", "")
    model_name = os.environ.get("ROLEPLEX_CONTRACT_OPENAI_MODEL", "")
    base_url = os.environ.get("ROLEPLEX_CONTRACT_OPENAI_BASE_URL", "") or None
    if not api_key or not model_name:
        print("缺少 ROLEPLEX_CONTRACT_OPENAI_KEY 或 ROLEPLEX_CONTRACT_OPENAI_MODEL")
        return 1

    await init_db()
    now = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        settings_row = await session.get(InstanceSettings, 1)
        owner_id = settings_row.owner_user_id if settings_row else None
        if owner_id is None:
            print("实例还没有 Owner，请先在界面注册第一个账号")
            return 1

        config = await session.scalar(select(ModelConfig).where(
            ModelConfig.created_by == owner_id, ModelConfig.name == CONFIG_NAME,
        ))
        if config is None:
            config = ModelConfig(created_by=owner_id, name=CONFIG_NAME, created_at=now, capability_overrides_json={})
            session.add(config)
        config.provider_type = "openai_compatible"
        config.base_url = base_url
        config.api_key_encrypted = encrypt_api_key(api_key)
        await session.flush()

        role = await session.scalar(select(Role).where(Role.created_by == owner_id, Role.name == ROLE_NAME))
        if role is None:
            role = Role(created_by=owner_id, name=ROLE_NAME, created_at=now, active=True)
            session.add(role)
        role.system_prompt = "你是 Roleplex 的开发调试助手，请用中文简洁回答。"
        role.model_config_id = config.id
        role.model_name = model_name
        role.params_json = {"temperature": 0.3, "max_tokens": 1024}
        role.updated_at = now
        await session.commit()
        print(f"已就绪：模型配置「{CONFIG_NAME}」、角色「{ROLE_NAME}」，模型 {model_name}，Owner 用户 {owner_id}")
    return 0


if __name__ == "__main__":
    _load_env_file()
    raise SystemExit(asyncio.run(seed()))
