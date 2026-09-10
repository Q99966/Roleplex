"""后台服务日志必须有界、加密，并区分淘汰与过期。"""
from datetime import datetime, timedelta, timezone

import pytest
from test_workspace_commands import command_conversation, command_root, isolated_command_database


def test_log_ring_budget_and_cursor_gap():
    """持续双流输出遵守内存及分页字节预算，旧游标明确报告缺口。"""
    from app.runtime.logs import LogRing, PAGE_BYTES, RING_BYTES
    ring = LogRing()
    for index in range(200):
        ring.append('stderr' if index % 2 else 'stdout', '中' * 3000)
    assert ring.bytes <= RING_BYTES
    page = ring.page(0)
    assert page['gap'] is True
    after = page['next_seq']
    assert sum(item['bytes'] for item in page['items']) <= PAGE_BYTES
    while after < ring.sequence:
        page = ring.page(after)
        assert not page['gap']
        assert after < page['next_seq']
        after = page['next_seq']
    assert ring.page(after)['items'] == []


@pytest.mark.anyio
async def test_archived_logs_encryption_eviction_and_startup_expiry(command_root, isolated_command_database, monkeypatch):
    """预算淘汰与期限清理均不泄露正文，重启维护会真正移除过期密文。

    Args:
        command_root：本轮隔离目录。
        isolated_command_database：全新迁移数据库。
        monkeypatch：缩小本用例日志预算。
    """
    from sqlalchemy import update
    from app.db import SessionLocal
    from app.runtime import logs, registry
    from app.runtime.models import RuntimeEntry
    async with command_conversation(command_root) as (_client, _headers, cid, rid, wid):
        row = await registry.reserve(owner_id=1, conversation_id=cid, workspace_id=wid,
            execution_id='log-fixture', role_id=rid, tool_call_id='logs', tool_name='workspace_start_service', kind='service')
        await registry.finish(row.id, 'rejected')
        ring = logs.LogRing()
        ring.append('stdout', 'private-test-output')
        await logs.save(row.id, ring)
        saved = await registry.get(row.id)
        assert 'private-test-output' not in saved.log_encrypted
        assert logs.archived_page(saved, 0)['items'][0]['text'] == 'private-test-output'
        saved.id = 'different-resource'
        assert logs.archived_page(saved, 0)['availability'] == 'unavailable'
        monkeypatch.setattr(logs, 'WORLD_BYTES', 1)
        await logs.save(row.id, ring)
        assert logs.archived_page(await registry.get(row.id), 0)['availability'] == 'evicted'
        monkeypatch.setattr(logs, 'WORLD_BYTES', 16 * 1024 * 1024)
        await logs.save(row.id, ring)
        async with SessionLocal() as session:
            await session.execute(update(RuntimeEntry).where(RuntimeEntry.id == row.id).values(
                log_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
            await session.commit()
        await logs.maintain()
        saved = await registry.get(row.id)
        assert saved.log_encrypted is None
        assert logs.archived_page(saved, 0)['availability'] == 'expired'
