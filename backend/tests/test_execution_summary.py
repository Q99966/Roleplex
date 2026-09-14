"""T1 系统事实、终态、持久恢复与输入隔离回归。"""
import json

import pytest

from app.services.execution_evidence import tool_evidence, message_stop_reason
from test_workspace_commands import command_root, isolated_command_database, command_conversation, send_command
from test_workspace_edit import wait_reply


@pytest.mark.anyio
async def test_wait_reply_requires_message_terminal_status():
    """活动任务已清空但历史仍是旧版本时，测试必须继续等待消息终态。"""
    from types import SimpleNamespace

    class HistoryClient:
        """按真实跨查询窗口返回两次受控历史，不接触产品数据库。"""
        calls = 0

        async def get(self, path, *, headers):
            """Args:
                path：测试请求的历史路径。
                headers：占位认证头。
            """
            self.calls += 1
            return SimpleNamespace(json=lambda: {'active_generation_ids': [], 'items': [
                {'id': 2, 'sender_type': 'role', 'status': 'generating' if self.calls == 1 else 'stopped'}]})

    client = HistoryClient()
    result = await wait_reply(client, {}, 1, 1)
    assert result['status'] == 'stopped' and client.calls == 2


def test_stable_test_clock_does_not_relax_token_validation():
    """测试时钟仍拒绝未来签发与过期 Token，没有增加任何认证容差。"""
    from datetime import datetime, timedelta, timezone
    import jwt
    from accounts import stable_auth_clock
    with stable_auth_clock():
        for claim, value, error in [
            ('iat', datetime.now(timezone.utc) + timedelta(seconds=60), jwt.ImmatureSignatureError),
            ('exp', datetime.now(timezone.utc) - timedelta(seconds=60), jwt.ExpiredSignatureError),
        ]:
            token = jwt.encode({'sub': '1', claim: value}, 'clock-placeholder', algorithm='HS256')
            with pytest.raises(error):
                jwt.decode(token, 'clock-placeholder', algorithms=['HS256'])


def test_partial_commit_and_unknown_output_are_distinct():
    """部分提交不冒充全批成功，成功退出或输出正文不能证明文件已修改。"""
    evidence = tool_evidence('workspace_edit', 'failed', {'format': 'write-batch-v1', 'batch': {'items': [
        {'applied': True, 'path': 'private-name'}, {'applied': None}]}})
    assert evidence == {'effect_state': 'unknown', 'confirmed_applied_items': 1}
    assert tool_evidence('workspace_run_shell', 'success', {'text': 'all files applied'})['effect_state'] == 'unknown'
    assert tool_evidence('unknown_tool', 'success', {'commit_confirmed': True})['effect_state'] == 'unknown'
    assert tool_evidence('workspace_write', 'cancelled', {'format': 'write-v1', 'commit_confirmed': True,
        'write': {'availability': 'unavailable'}})['confirmed_applied_items'] == 1


def test_legacy_summary_is_not_injected_or_rebuilt():
    """旧记录只提供兼容停止原因，摘要永不进入正常正文或未知 part 占位。"""
    from app.models import Message
    from app.context.projection import project_message
    body = '  原始回答\n    缩进保持\n'
    row = Message(sender_type='role', sender_id=1, status='done', meta_json={'execution_summary_version': 1},
        parts_json=[{'type': 'text', 'text': body}, {'type': 'execution_summary', 'version': 1,
        'stop_reason': 'graph_budget', 'confirmed_applied_items': 50}])
    assert project_message(row, target_role_id=1).content == body
    row.status = 'stopped'
    assert message_stop_reason(row) == 'graph_budget'
    assert '用户停止' not in project_message(row, target_role_id=1).content
    row.sender_type = 'user'
    assert message_stop_reason(row) is None


@pytest.mark.anyio
async def test_budget_reason_and_tool_evidence_persist_without_summary(command_root, isolated_command_database):
    """通过实际文件工厂、reducer、历史、重启恢复核对提交与停止原因。

    Args:
        command_root：本轮隔离文件根。
        isolated_command_database：本用例迁移创建的新数据库。
    """
    from app.db import SessionLocal, recover_interrupted_messages
    from app.models import Message
    from app.context.projection import project_message
    from app.services.chat import _finalize

    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        role = next(row for row in (await client.get('/api/roles', headers=headers)).json() if row['id'] == rid)
        assert (await client.put(f'/api/roles/{rid}', headers=headers,
            json={**role, 'builtin_tools': ['workspace_write', 'workspace_list']})).status_code == 200
        await client.patch(f'/api/workspaces/{wid}', headers=headers, json={'file_tools_enabled': True})
        sent = await send_command(client, headers, cid, '[EXECUTION_FACTS_FAKE]')
        message = await wait_reply(client, headers, cid, sent['message']['id'])
        assert message['status'] == 'stopped'
        assert (command_root / 'facts-proof.txt').read_text() == 'controlled-facts'
        assert message['stop_reason'] == 'graph_budget'
        assert not any(part['type'] == 'execution_summary' for part in message['parts_json'])
        call = next(part for part in message['parts_json'] if part.get('tool_name') == 'workspace_write')
        assert call['confirmed_applied_items'] == 1 and call['effect_state'] == 'applied'
        assert 'facts-proof' not in json.dumps(message['parts_json'])
        assert 'controlled-facts' not in json.dumps(message['parts_json'])
        await _finalize(sent['generation_id'], 'completed', 'late-overwrite')
        history = (await client.get(f'/api/conversations/{cid}/messages', headers=headers)).json()['items']
        assert next(row for row in history if row['id'] == message['id']) == message
        forged = await client.post(f'/api/conversations/{cid}/messages', headers=headers,
            json={'parts': [{'type': 'text', 'text': 'probe'}, {'type': 'execution_summary', 'version': 1}]})
        assert forged.status_code == 422
        async with SessionLocal() as session:
            row = await session.get(Message, message['id'])
            projected = project_message(row, target_role_id=rid)
            assert projected is not None
            assert '用户停止' not in projected.content
            assert 'confirmed_applied_items' not in projected.content
            # 模拟保存过提交凭据但终态尚未落库即重启，不读取当前文件补造事实。
            row.status = 'generating'
            row.parts_json = row.parts_json + [{'type': 'tool_call', 'call_id': 'pending', 'status': 'running'}]
            await session.commit()
        await recover_interrupted_messages()
        async with SessionLocal() as session:
            row = await session.get(Message, message['id'])
            assert row.status == 'interrupted' and message_stop_reason(row) == 'interrupted'
            assert not any(part.get('type') == 'execution_summary' for part in row.parts_json)
            call = next(part for part in row.parts_json if part.get('tool_name') == 'workspace_write')
            assert call['confirmed_applied_items'] == 1
            assert project_message(row, target_role_id=rid) is None


@pytest.mark.anyio
async def test_provider_failure_preserves_committed_fact(command_root, isolated_command_database, monkeypatch):
    """工具真实提交后厂商失败仍保留调用证据与停止原因，不生成摘要或追加解释。

    Args:
        command_root：本轮隔离文件目录。
        isolated_command_database：本用例新数据库。
        monkeypatch：只将厂商替换为第二回合超时的确定性模型。
    """
    from app.agent.fake_provider import ScriptedChatModel, ScriptedTurn
    from app.services import chat

    class FailAfterWrite(ScriptedChatModel):
        """先走真实文件工具，下一次模型请求稳定失败。"""
        def _next_turn(self):
            """第二次请求注入超时，不伪造任何工具结果。"""
            if self.index:
                raise TimeoutError('controlled-timeout')
            return super()._next_turn()

    model = FailAfterWrite(turns=[ScriptedTurn(tool_calls=[{
        'name': 'workspace_write', 'args': {'path': 'failure-proof.txt', 'content': 'committed'}, 'id': 'write-proof'}])], delay=0)
    monkeypatch.setattr(chat, 'fake_reply_model', lambda *args, **kwargs: model)
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        role = next(row for row in (await client.get('/api/roles', headers=headers)).json() if row['id'] == rid)
        await client.put(f'/api/roles/{rid}', headers=headers, json={**role, 'builtin_tools': ['workspace_write']})
        await client.patch(f'/api/workspaces/{wid}', headers=headers, json={'file_tools_enabled': True})
        sent = await send_command(client, headers, cid, 'probe')
        message = await wait_reply(client, headers, cid, sent['message']['id'])
        assert message['status'] == 'error'
        assert (command_root / 'failure-proof.txt').read_text() == 'committed'
        call = next(part for part in message['parts_json'] if part.get('tool_name') == 'workspace_write')
        assert call['confirmed_applied_items'] == 1
        assert message['stop_reason'] == 'provider_failed'
        assert not any(part.get('type') == 'execution_summary' for part in message['parts_json'])


@pytest.mark.parametrize('status', ['error', 'interrupted'])
def test_failed_history_does_not_inject_old_facts(status):
    """失败或中断记录不再通过历史摘要替代恢复工作流程。

    Args:
        status：需要显式恢复设计的终态。
    """
    from app.context.projection import project_message
    from app.models import Message
    row = Message(sender_type='role', sender_id=1, status=status, meta_json={'execution_summary_version': 1},
        parts_json=[{'type': 'text', 'text': 'untrusted-body'}, {'type': 'execution_summary', 'version': 1}])
    assert project_message(row, target_role_id=1) is None


def test_context_budget_does_not_reintroduce_statistics():
    """完整消息超预算时按边界裁剪，不再退化为统计摘要。"""
    from app.context.builder import _ProjectedHistory, _select_history
    from app.context.projection import project_message
    from app.models import Message
    from app.context.budget import estimate_messages_tokens
    row = Message(id=1, sender_type='role', sender_id=1, status='done', pinned=False,
        meta_json={'execution_summary_version': 1}, parts_json=[{'type': 'text', 'text': 'x' * 10000},
        {'type': 'execution_summary', 'version': 1, 'confirmed_applied_items': 1}])
    body = project_message(row, target_role_id=1)
    selected, used, _ = _select_history([_ProjectedHistory(row, body, estimate_messages_tokens([body]))],
        fixed_tokens=10, input_budget=600, pinned_budget=0)
    assert selected == () and used == 0


def test_directory_side_effect_is_not_reported_as_no_effect():
    """文件提交计数保持零，目录副作用不能被未提交文件状态抹去。"""
    for value in [
        {'format': 'write-v1', 'write': {'availability': 'not_executed', 'created_parent_count': 1}},
        {'format': 'write-batch-v1', 'batch': {'items': [{'applied': False, 'created_parent_count': 2}]}},
    ]:
        assert tool_evidence('workspace_write', 'failed', value) == {'effect_state': 'unknown', 'confirmed_applied_items': 0}
