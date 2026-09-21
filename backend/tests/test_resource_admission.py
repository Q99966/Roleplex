"""受控资源共享读、排他写与公平取消；使用事件证明重叠而非耗时猜测。"""
import asyncio
from pathlib import Path
import pytest


async def queued(pool, count):
    for _ in range(100):
        if len(pool.waiting) == count: return
        await asyncio.sleep(0)
    raise AssertionError('资源请求没有进入预期排队状态')


@pytest.mark.anyio
async def test_shared_read_and_writer_fairness(tmp_path):
    from app.workspaces.resource_admission import ResourceAdmission
    pool = ResourceAdmission()
    release_read, release_write, writer_started, late_started = [asyncio.Event() for _ in range(4)]
    entered = []
    async def reader(marker):
        async with pool.claim(tmp_path, 'read'):
            entered.append(marker)
            await release_read.wait()
    first = asyncio.create_task(reader('first'))
    second = asyncio.create_task(reader('second'))
    for _ in range(100):
        if len(entered) == 2: break
        await asyncio.sleep(0)
    assert len(entered) == 2
    async def writer():
        async with pool.claim(tmp_path, 'write'):
            writer_started.set(); await release_write.wait()
    write = asyncio.create_task(writer()); await queued(pool, 1)
    async def late_reader():
        async with pool.claim(tmp_path, 'read'): late_started.set()
    late = asyncio.create_task(late_reader()); await queued(pool, 2)
    assert not writer_started.is_set() and not late_started.is_set()
    release_read.set(); await asyncio.gather(first, second)
    await asyncio.wait_for(writer_started.wait(), 2)
    assert not late_started.is_set()
    release_write.set(); await asyncio.gather(write, late)
    assert late_started.is_set() and not pool.active and not pool.waiting


@pytest.mark.anyio
async def test_alias_overlap_independent_roots_and_cancel(tmp_path):
    from app.workspaces.resource_admission import ResourceAdmission
    pool = ResourceAdmission()
    parent = tmp_path / 'parent'; parent.mkdir()
    child = parent / 'child'; child.mkdir()
    alias = tmp_path / 'alias'; alias.symlink_to(parent, target_is_directory=True)
    other = tmp_path / 'other'; other.mkdir()
    release, started = asyncio.Event(), asyncio.Event()
    async def hold():
        async with pool.claim(parent, 'write'): started.set(); await release.wait()
    holder = asyncio.create_task(hold()); await started.wait()
    entered = []
    async def claim(root):
        async with pool.claim(root, 'read'): entered.append(root)
    nested = asyncio.create_task(claim(child)); aliased = asyncio.create_task(claim(alias))
    await queued(pool, 2)
    await asyncio.wait_for(claim(other), 2)
    assert entered == [other]
    nested.cancel(); nested.cancel()
    with pytest.raises(asyncio.CancelledError): await nested
    await queued(pool, 1)
    release.set(); await asyncio.gather(holder, aliased)
    assert entered == [other, alias] and not pool.active and not pool.waiting


@pytest.mark.anyio
async def test_timeout_and_read_upgrade_never_leak_claims(tmp_path, monkeypatch):
    from app.config import settings
    from app.workspaces.resource_admission import ResourceAdmission
    from app.workspaces.files import WorkspaceFileError
    pool = ResourceAdmission()
    monkeypatch.setattr(settings, 'workspace_resource_wait_seconds', .05)
    async with pool.claim(tmp_path, 'read'):
        with pytest.raises(WorkspaceFileError, match='WORKSPACE_RESOURCE_UPGRADE_REQUIRED'):
            async with pool.claim(tmp_path, 'write'): pass
        async def blocked():
            async with pool.claim(tmp_path, 'write'): raise AssertionError('排他请求不能在共享读期间进入')
        with pytest.raises(WorkspaceFileError, match='WORKSPACE_RESOURCE_TIMEOUT'):
            await asyncio.create_task(blocked())
        assert len(pool.active) == 1 and not pool.waiting
    assert not pool.active


@pytest.mark.anyio
async def test_native_file_commit_mutex_and_stale_version(tmp_path, monkeypatch):
    from app.workspaces.files import WorkspaceFileService, WorkspaceFileError
    from app.services import file_effects
    first_entered, release = asyncio.Event(), asyncio.Event()
    prepared = []
    async def checkpoint(execution_id, **facts):
        if facts['state'] == 'prepared':
            prepared.append(execution_id)
            if execution_id == 'first': first_entered.set(); await release.wait()
        return False
    monkeypatch.setattr(file_effects, 'checkpoint', checkpoint)
    first = WorkspaceFileService(root=tmp_path, execution_id='first')
    second = WorkspaceFileService(root=tmp_path, execution_id='second')
    a = asyncio.create_task(first.write('a.txt', 'first'))
    await first_entered.wait()
    b = asyncio.create_task(second.write('b.txt', 'second'))
    from app.workspaces.resource_admission import current
    await queued(current(), 1)
    assert prepared == ['first']
    release.set(); await asyncio.gather(a, b)
    assert prepared == ['first', 'second']
    read = await first.read('a.txt')
    await second.write('a.txt', 'new', expected_sha256=read.sha256)
    with pytest.raises(WorkspaceFileError, match='WORKSPACE_FILE_REVISION_CONFLICT'):
        await first.write('a.txt', 'stale', expected_sha256=read.sha256)
    assert (tmp_path / 'a.txt').read_text() == 'new'
