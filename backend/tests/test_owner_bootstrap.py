"""Owner 原子初始化测试。

覆盖三条：
1. 首个成功注册的账号成为 Owner，之后注册的一律是 Guest；
2. 单例记录已被认领时，新注册者不会再次认领（认领是有条件的，不是无条件置位）；
3. 并发注册的结果只有一个 Owner。

这些都要求数据库处于"还没有任何账号"的状态，因此每个用例使用独立的新库，
而不是共用整轮测试库、只换用户名——后者无法覆盖"第一个注册者"这件事本身。

**关于第 3 条的判别力（实测结论，2026-08-20）**：在 SQLite 上这条用例无法区分
"原子条件 UPDATE"与"先查后写"。注册在 `session.flush()` 插入用户时就取得写锁，
而读取和更新单例记录都在同一个写事务内，SQLite 的全库单写者会把 8 个并发注册
完全串行化——实测把"先查后写"实现中的临界区人为拉长到 0.3 秒，8 个请求耗时
约 2.4 秒（≈ 8×0.3）而不是 0.3 秒，且结果仍然只有一个 Owner。

也就是说当前的原子性由 SQLite 的单写者锁提供，条件 UPDATE 是为切换到
PostgreSQL 准备的：届时并发写入真正并行，这条用例才具备判别力。在此之前，
真正能挡住回归的是第 2 条那个确定性用例。
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

# 合规的固定占位口令，仅用于测试库。
BOOTSTRAP_PASSWORD = "Roleplex-Bootstrap-1"


def _purge_app_modules() -> None:
    """从模块缓存中移除 app 包，使下次导入按当前环境变量重新建立引擎。"""
    for name in [module for module in list(sys.modules) if module == "app" or module.startswith("app.")]:
        del sys.modules[name]


@pytest.fixture
async def fresh_app(tmp_path: Path):
    """提供一个绑定到全新空数据库的应用实例。

    数据库引擎在 `app.db` 导入时按 `DATABASE_URL` 建立，是模块级单例，
    因此换库必须连同 app 包一起重新导入。用例结束后还原环境变量并再次清空
    模块缓存，使后续用例回到 conftest 指定的整轮测试库。

    Args:
        tmp_path：pytest 提供的用例级临时目录。

    Yields:
        已完成迁移、尚无任何账号的 FastAPI 应用。
    """
    previous_url = os.environ["DATABASE_URL"]
    database = tmp_path / "bootstrap.db"
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{database.as_posix()}"
    _purge_app_modules()

    from app.main import app
    from app.db import engine

    async with app.router.lifespan_context(app):
        yield app
    # 释放文件句柄，否则 Windows 上临时目录清理会失败。
    await engine.dispose()

    os.environ["DATABASE_URL"] = previous_url
    _purge_app_modules()


async def _register(client: AsyncClient, username: str) -> dict:
    """注册一个账号并返回响应体。

    Args:
        client：绑定到目标应用的异步客户端。
        username：账号名，调用方保证同一用例内不重复。
    """
    response = await client.post("/api/auth/register", json={
        "username": username, "password": BOOTSTRAP_PASSWORD, "nickname": f"账号 {username}",
    })
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.anyio
async def test_first_registration_claims_owner(fresh_app):
    """首个注册者成为 Owner，后续注册者是 Guest 且拿不到配置类接口。"""
    async with AsyncClient(transport=ASGITransport(app=fresh_app), base_url="http://test") as client:
        first = await _register(client, "bootstrap_owner")
        assert first["user"]["is_owner"] is True

        second = await _register(client, "bootstrap_guest")
        assert second["user"]["is_owner"] is False

        # Guest 身份在配置类接口上被拒，证明 is_owner 确实生效而不只是响应字段。
        guest_headers = {"Authorization": f"Bearer {second['access_token']}"}
        denied = await client.get("/api/model-configs", headers=guest_headers)
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "OWNER_REQUIRED"

        owner_headers = {"Authorization": f"Bearer {first['access_token']}"}
        assert (await client.get("/api/model-configs", headers=owner_headers)).status_code == 200


@pytest.mark.anyio
async def test_registration_does_not_reclaim_an_existing_owner(fresh_app):
    """单例记录已被认领后，新注册者不会再次成为 Owner。

    本用例确定性地验证"认领是有条件的"：它直接检查单例记录的指向，
    因此把认领写成无条件置位会立刻失败，且不依赖任何调度时序。
    并发用例同样能抓住无条件置位，但抓不住"先查后写"（原因见模块 docstring）。
    """
    from app.db import SessionLocal
    from app.models import InstanceSettings

    async with AsyncClient(transport=ASGITransport(app=fresh_app), base_url="http://test") as client:
        owner = await _register(client, "existing_owner")
        assert owner["user"]["is_owner"] is True

        # 直接确认单例记录确实指向首个注册者，而不是仅凭响应字段判断。
        async with SessionLocal() as session:
            settings_row = await session.get(InstanceSettings, 1)
            assert settings_row is not None
            assert settings_row.owner_user_id == owner["user"]["id"]

        later = await _register(client, "later_account")
        assert later["user"]["is_owner"] is False

        # 认领不会被后来者改写。
        async with SessionLocal() as session:
            settings_row = await session.get(InstanceSettings, 1)
            assert settings_row.owner_user_id == owner["user"]["id"]


@pytest.mark.anyio
async def test_concurrent_registration_produces_single_owner(fresh_app):
    """并发注册的结果只有一个 Owner，其余全部是 Guest。

    注意本用例在 SQLite 上只验证"结果正确"，不验证"实现原子"：写事务被单写者锁
    串行化，任何实现都会得到同一结果（实测依据见模块 docstring）。切换到
    PostgreSQL 后它才真正开始区分条件 UPDATE 与先查后写。
    """
    async with AsyncClient(transport=ASGITransport(app=fresh_app), base_url="http://test") as client:
        results = await asyncio.gather(
            *(_register(client, f"race_{index}") for index in range(8)),
            return_exceptions=True,
        )

    failures = [item for item in results if isinstance(item, BaseException)]
    assert not failures, f"并发注册不应失败：{failures}"

    owners = [item for item in results if item["user"]["is_owner"]]
    assert len(owners) == 1, f"Owner 数量应为 1，实际 {len(owners)}"
    assert len(results) == 8
