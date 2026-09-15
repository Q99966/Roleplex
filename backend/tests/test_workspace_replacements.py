"""T3 多片段修改以同一原始版本校验，全部通过后一次提交。"""
import hashlib
import pytest
from test_workspace_commands import command_root, isolated_command_database


@pytest.mark.anyio
async def test_replacements_match_original_and_commit_once(command_root, monkeypatch):
    """Args:
        command_root：隔离文件目录。
        monkeypatch：观察实际原子替换次数，不改变文件结果。
    """
    from app.workspaces.files import WorkspaceFileService
    import os
    original = '甲=1\r\n乙=2\r\n丙=3\n'
    target = command_root / 'sample.txt'
    target.write_bytes(original.encode())
    service = WorkspaceFileService(root=command_root, execution_id='replacements')
    calls = []
    replace = os.replace
    def observed(source, destination):
        """Args:
            source：受控临时文件。
            destination：原子替换目标。
        """
        calls.append(True)
        return replace(source, destination)
    monkeypatch.setattr(os, 'replace', observed)
    pairs = [{'old_text': old, 'new_text': new} for old, new in [('丙=3', '丙=30'), ('甲=1', '甲=10'), ('乙=2', '乙=20')]]
    result = await service.edit('sample.txt', replacements=pairs, expected_sha256=hashlib.sha256(original.encode()).hexdigest())
    assert target.read_bytes() == '甲=10\r\n乙=20\r\n丙=30\n'.encode()
    assert result.sha256 == hashlib.sha256(target.read_bytes()).hexdigest()
    assert len(calls) == 1


@pytest.mark.anyio
@pytest.mark.parametrize('pairs,code,index', [
    ([('a', 'x'), ('b', 'y'), ('missing', 'z')], 'WORKSPACE_EDIT_MATCH_NOT_FOUND', 3),
    ([('abc', 'x'), ('missing', 'y')], 'WORKSPACE_EDIT_MATCH_NOT_FOUND', 2),
    ([('abc', 'created'), ('created', 'y')], 'WORKSPACE_EDIT_MATCH_NOT_FOUND', 2),
    ([('abc', 'x'), ('bc', 'y')], 'WORKSPACE_EDIT_OVERLAP', 2),
    ([('abc', 'x'), ('abc', 'y')], 'WORKSPACE_EDIT_OVERLAP', 2),
    ([('abc', 'x'), ('z', 'y')], 'WORKSPACE_EDIT_MATCH_AMBIGUOUS', 2),
])
async def test_any_invalid_replacement_leaves_file_unchanged(command_root, pairs, code, index):
    """Args:
        command_root：本轮目录。
        pairs：无效的受控替换请求。
        code：稳定错误码。
        index：按输入顺序的一基失败序号。
    """
    from app.workspaces.files import WorkspaceFileService, WorkspaceFileError
    content = b'abc z z'
    target = command_root / 'sample.txt'; target.write_bytes(content)
    service = WorkspaceFileService(root=command_root, execution_id='invalid-replacements')
    with pytest.raises(WorkspaceFileError) as error:
        await service.edit('sample.txt', replacements=[{'old_text': old, 'new_text': new} for old, new in pairs], expected_sha256=hashlib.sha256(content).hexdigest())
    assert error.value.code == code and error.value.details['replacement_index'] == index
    assert target.read_bytes() == content


def test_multi_edit_schema_rejects_mixing_and_preserves_legacy():
    """原始键混用、空数组及空旧片段均拒绝，旧调用保持兼容。"""
    from pydantic import ValidationError
    from app.workspaces.tools import WorkspaceEditInput
    from app.workspaces.batch_mutation import validate_items
    digest = '0' * 64
    pair = {'old_text': 'a', 'new_text': ''}
    value = {'path': 'a', 'expected_sha256': digest, 'replacements': [pair]}
    assert WorkspaceEditInput.model_validate(value).replacements[0].new_text == ''
    assert validate_items('edit', [value])[0] == value
    assert WorkspaceEditInput(path='a', old_text='a', new_text='b', expected_sha256=digest).new_text == 'b'
    for bad in [dict(value, old_text=None), dict(value, new_text='x'), dict(value, replacements=None),
                dict(value, replacements=[]), dict(value, replacements=[pair] * 33), dict(value, replacements=[{'old_text': '', 'new_text': 'b'}]),
                dict(value, items=[value]), {'items': [dict(value, old_text=None)]}]:
        with pytest.raises(ValidationError):
            WorkspaceEditInput.model_validate(bad)


@pytest.mark.anyio
async def test_replacements_order_adjacency_budget_and_noop(command_root):
    """Args:
        command_root：本轮边界文件目录。
    """
    from app.workspaces.files import WorkspaceFileService, WorkspaceFileError
    from app.workspaces.diffs import change_metadata
    service = WorkspaceFileService(root=command_root, execution_id='replacement-boundaries')
    pairs = [{'old_text': 'a', 'new_text': 'b'}, {'old_text': 'b', 'new_text': 'c'}]
    for name, values in [('first', pairs), ('second', list(reversed(pairs)))]:
        (command_root / name).write_bytes(b'ab')
        await service.edit(name, replacements=values, expected_sha256=hashlib.sha256(b'ab').hexdigest())
        assert (command_root / name).read_bytes() == b'bc'
    before = b'keep'
    (command_root / 'noop').write_bytes(before)
    captured = []
    await service.edit('noop', replacements=[{'old_text': 'keep', 'new_text': 'keep'}], expected_sha256=hashlib.sha256(before).hexdigest(),
        capture_applied=lambda a,b: captured.append(change_metadata('noop', a,b)))
    assert captured[0]['files'][0]['operation'] == 'unchanged'
    for pairs in [[{'old_text':'keep', 'new_text':'x' * 65533}], [{'old_text':'keep', 'new_text':'\ud800'}]]:
        with pytest.raises(WorkspaceFileError):
            await service.edit('noop', replacements=pairs, expected_sha256=hashlib.sha256(before).hexdigest())
        assert (command_root / 'noop').read_bytes() == before


@pytest.mark.anyio
async def test_replacements_revision_race_cancel_and_final_size(command_root, monkeypatch):
    """Args:
        command_root：本轮受控目录。
        monkeypatch：在提交边界模拟外部文件变更。
    """
    import asyncio
    from app.workspaces.files import WorkspaceFileService, WorkspaceFileError
    service = WorkspaceFileService(root=command_root, execution_id='replacement-race')
    target = command_root / 'source'; target.write_bytes(b'old')
    original = service._replace_existing
    def changed(path, current, encoded, capture):
        """Args:
            path：受控目标。
            current：原始内容。
            encoded：候选新内容。
            capture：提交凭据回调。
        """
        target.write_bytes(b'external')
        return original(path, current, encoded, capture)
    monkeypatch.setattr(service, '_replace_existing', changed)
    with pytest.raises(WorkspaceFileError) as error:
        await service.edit('source', replacements=[{'old_text':'old', 'new_text':'new'}], expected_sha256=hashlib.sha256(b'old').hexdigest())
    assert error.value.code == 'WORKSPACE_FILE_REVISION_CONFLICT' and target.read_bytes() == b'external'
    assert not list(command_root.glob('.roleplex-*'))
    await service._lock.acquire()
    task = asyncio.create_task(service.edit('source', replacements=[{'old_text':'external','new_text':'new'}], expected_sha256=hashlib.sha256(b'external').hexdigest()))
    await asyncio.sleep(0); task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    service._lock.release()
    assert target.read_bytes() == b'external'
    large = b'a' + b'x' * (1024 * 1024 - 1); target.write_bytes(large)
    with pytest.raises(WorkspaceFileError) as error:
        await service.edit('source', replacements=[{'old_text':'a','new_text':'more'}], expected_sha256=hashlib.sha256(large).hexdigest())
    assert error.value.code == 'WORKSPACE_FILE_TOO_LARGE' and target.read_bytes() == large

@pytest.mark.anyio
async def test_real_tool_factory_accepts_single_and_batch_replacements(command_root, isolated_command_database):
    """Args:
        command_root：受控目录。
        isolated_command_database：每例重放迁移，覆盖真实 schema 到工具入口。
    """
    import json
    from test_workspace_commands import command_conversation
    from test_write_diagnostics import mutation_tools
    from app.agent.tool_capture import capture_input
    from app.agent.tools import summarize_tool_args
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        tools, _, _ = await mutation_tools(client, headers, cid, rid, wid)
        for name, batch in [('single.txt', False), ('batch.txt', True)]:
            (command_root / name).write_bytes(b'alpha=1\nbeta=2\n')
            value = {'path': name, 'expected_sha256': hashlib.sha256(b'alpha=1\nbeta=2\n').hexdigest(), 'replacements': [
                {'old_text':'alpha=1','new_text':'alpha=10'}, {'old_text':'beta=2','new_text':'beta=20'}]}
            output = await tools['workspace_edit'].ainvoke({'items':[value]} if batch else value)
            body = json.loads(output.split('] ',1)[-1])
            assert (body.get('status') == 'success') if batch else ('sha256' in body)
            assert (command_root / name).read_bytes() == b'alpha=10\nbeta=20\n'
            captured = capture_input('workspace_edit', value)
            assert 'alpha=1' not in captured['text'] and 'replacement_count' in captured['text']
            assert 'alpha=1' not in summarize_tool_args('workspace_edit', value)


@pytest.mark.anyio
async def test_maximum_replacements_commit_even_when_diff_is_unavailable(command_root):
    """32 处替换成功提交，展示额度不足不能改变文件提交事实。

    Args:
        command_root：本轮受控大文件目录。
    """
    from app.workspaces.files import WorkspaceFileService
    from app.workspaces.diffs import DiffPool, change_metadata
    before = (''.join(f'key-{i:02}=old\n' for i in range(32)) + 'x' * (130 * 1024)).encode()
    expected = before.replace(b'=old', b'=new')
    target = command_root / 'large.txt'
    target.write_bytes(before)
    service = WorkspaceFileService(root=command_root, execution_id='max-replacements')
    pool = DiffPool()
    tickets = []

    def capture(old, new):
        """Args:
            old：实际提交的原始字节。
            new：实际提交的最终字节。
        """
        tickets.append(pool.submit(old, new, change_metadata('large.txt', old, new)))

    try:
        result = await service.edit('large.txt',
            replacements=[{'old_text': f'key-{i:02}=old', 'new_text': f'key-{i:02}=new'} for i in range(32)],
            expected_sha256=hashlib.sha256(before).hexdigest(), capture_applied=capture)
        assert target.read_bytes() == expected
        assert result.sha256 == hashlib.sha256(expected).hexdigest()
        assert len(tickets) == 1
        detail = await tickets[0].result()
        assert detail['availability'] == 'unavailable' and detail['reason'] == 'input_budget'
        assert detail['files'][0]['applied'] is True
    finally:
        await pool.close()
