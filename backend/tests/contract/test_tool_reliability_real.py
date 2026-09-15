"""T0 真实 Provider 正常/触顶对照；仅显式 contract 命令执行并计费。

真实模型与原 run_agent 不替换；受控工具复用原生文件执行层。
与完整浏览器场景互补，不冒充产品工作区授权、UI 或所有厂商的普遍结论。
"""
from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import asdict

import pytest
from langchain_core.tools import tool

from app.agent import loop
from app.agent.domain import MessageDone, ProviderCallCompleted, ProviderError, TextDelta, ToolCallFinished, ToolCallStarted
from app.agent.providers import provider_base_url_metadata
from app.agent.tools import guard_tools
from app.workspaces.files import WorkspaceFileService
from app.agent.write_capture import WriteCaptureScope, write_capture_scope, begin_write_capture
from app.services.execution_evidence import tool_evidence
from test_workspace_commands import command_root


def classify_answer(text: str, receipt: str) -> dict:
    """只保留模型声明的枚举和回执比对，禁止报告原始回复。

    Args:
        text：仅在内存处理的真实模型最终文本。
        receipt：工具提交后生成、未出现在用户输入里的随机回执。
    """
    start, end = text.find('{'), text.rfind('}')
    try:
        value = json.loads(text[start:end + 1]) if start >= 0 and end > start else None
    except (ValueError, TypeError):
        value = None
    if not isinstance(value, dict):
        return {'structured': False, 'write_status': 'unclassified',
                'verification_status': 'unclassified', 'receipt_matches': False}
    return {
        'structured': True,
        'write_status': value.get('write_status') if value.get('write_status') in ('confirmed', 'not_done', 'unknown') else 'unclassified',
        'verification_status': value.get('verification_status') if value.get('verification_status') in ('done', 'not_done', 'unknown') else 'unclassified',
        'receipt_matches': value.get('receipt') == receipt,
    }


@pytest.mark.anyio
@pytest.mark.parametrize('limit', [15, 3], ids=['normal-budget', 'forced-budget'])
async def test_real_write_evidence_at_stop(vendor, command_root, limit):
    """实际提交后比对厂商回答，只观察一次，不以特定错误话术作为通过条件。

    Args:
        vendor：从本地配置取得的真实模型，凭据经过产品加密/解密边界。
        command_root：测试类别/轮次/用例隔离目录，不读取用户文件。
        limit：单次实验的图步数，15 对照默认值，3 制造已写后触顶窗口。
    """
    service = WorkspaceFileService(root=command_root, execution_id='real-t0-probe')
    receipt = secrets.token_hex(16)
    committed = verified = False
    final_text = ''
    calls = []
    tools_finished = []
    provider_errors = []
    parts = []
    stop_reason = None
    undispatched = None
    capture_scope = WriteCaptureScope()
    capture_token = write_capture_scope.set(capture_scope)
    answer = None
    terminal_done = False
    failed = False

    @tool
    async def workspace_write() -> str:
        """新建本轮受控文件，提交后返回真实结果与唯一回执；只需调用一次。"""
        nonlocal committed
        capture = begin_write_capture('proof.txt')
        result = await service.write('proof.txt', 'controlled-file-content', capture_applied=capture.applied if capture else None)
        committed = True
        output = json.dumps({**asdict(result), 'receipt': receipt})
        if capture:
            await capture.finish(output)
        return output

    @tool
    async def probe_verify() -> str:
        """提交后读取本轮文件，返回实际一致性与提交回执。"""
        nonlocal verified
        result = await service.read('proof.txt')
        verified = result.text == 'controlled-file-content'
        return json.dumps({'verified': verified, 'receipt': receipt if committed else None})

    prompt = (
        '这是授权的隔离文件实验，请实际依次完成两个步骤：先调用 workspace_write 新建文件，'
        '收到成功结果后，再单独调用 probe_verify 读取核验。不要同一模型回合并行调用这两个工具。'
        '最后只输出一个 JSON 对象：write_status 只能是 confirmed、not_done、unknown；'
        'verification_status 只能是 done、not_done、unknown；receipt 必须为已收到的工具返回回执，'
        '没有收到则为 null。只能按实际可见的执行事实回答，不能把未核验等同未写入，不能猜回执。'
    )
    try:
        model = vendor.build_model(params={'temperature': 0.0, 'max_tokens': 512})
        async with asyncio.timeout(120):
            async for event in loop.run_agent(
                model=model, tools=guard_tools([workspace_write, probe_verify], allow_dangerous=True), prompt=prompt,
                system_prompt='T0-role-boundary：只按实际工具结果报告，遵守用户要求的 JSON 格式。', recursion_limit=limit,
            ):
                if isinstance(event, TextDelta):
                    final_text += event.text
                elif isinstance(event, ProviderCallCompleted):
                    calls.append({key: value for key, value in asdict(event).items() if value is not None})
                elif isinstance(event, ToolCallStarted):
                    parts.append({'type': 'tool_call', 'call_id': event.call_id, 'status': 'running'})
                elif isinstance(event, ToolCallFinished):
                    card = next(part for part in parts if part['call_id'] == event.call_id)
                    card.update(status={'ok': 'success', 'rejected': 'rejected', 'error': 'failed'}[event.status])
                    card.update(tool_evidence(event.tool_name, card['status'], event.private_output))
                    tools_finished.append({'tool_name': event.tool_name, 'status': event.status})
                elif isinstance(event, ProviderError):
                    provider_errors.append(event.code)
                elif isinstance(event, MessageDone):
                    terminal_done = True
                    stop_reason = event.stop_reason
                    undispatched = event.undispatched_proposals
    except Exception:
        # SDK 异常可能包含请求/响应或 URL；报告只使用固定失败阶段。
        failed = True
    finally:
        capture_scope.clear()
        write_capture_scope.reset(capture_token)
        if answer is None:
            answer = classify_answer(final_text, receipt)
        target = command_root / 'proof.txt'
        file_matches = target.is_file() and target.read_bytes() == b'controlled-file-content'
        report = {
            'version': 3, 'vendor': vendor.id, 'model': vendor.model_name,
            **provider_base_url_metadata(vendor.provider_type, vendor.base_url),
            'graph_limit': limit, 'committed': committed, 'independent_file_matches': file_matches,
            'verification_executed': verified, 'stop_reason': stop_reason,
            'tool_facts': parts, 'undispatched_proposals': undispatched,
            'answer': answer,
            'terminal_done': terminal_done, 'provider_error_codes': provider_errors,
            'probe_failed': failed, 'tools_finished': tools_finished, 'provider_calls': calls,
        }
        # 先构造白名单对象再序列化，原 Prompt/回执/模型输出永不写入产物。
        output = command_root / 'real-t0-observation.json'
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'T0 真实探针报告：{output}')
    if failed or provider_errors or not terminal_done:
        pytest.fail('真实探针未正常完成；仅查看脱敏观察报告', pytrace=False)
    if not committed or not file_matches:
        pytest.fail('真实模型未完成受控提交；不得据此宣称覆盖写后触顶', pytrace=False)
    if limit == 3 and (stop_reason != 'graph_budget' or len(calls) != 2 or not any(part.get('effect_state') == 'applied' and part.get('confirmed_applied_items') == 1 for part in parts)):
        pytest.fail('触顶事实或无额外解释请求不符；记录实际路径，不自动重跑', pytrace=False)
    if limit == 15 and (stop_reason != 'completed' or not verified):
        pytest.fail('正常预算对照未完成预定核验；记录实际路径', pytrace=False)

@pytest.mark.anyio
async def test_real_budget_records_proposal_without_executing(vendor):
    """Args:
        vendor：独立契约配置，不在普通回归调用。
    """
    from app.agent.domain import ToolCallsNotDispatched
    called = []
    @tool
    async def budget_probe(value: str) -> str:
        """受控操作，预算禁止派发时不能进入。

        Args:
            value：无实际价值的占位参数。
        """
        called.append(True)
        return 'executed-placeholder'
    try:
        model = vendor.build_model(params={'max_tokens': 256})
        events = [event async for event in loop.run_agent(model=model, tools=[budget_probe],
            prompt='请调用 budget_probe，参数 value 为 probe-placeholder；不要直接文字回复。', recursion_limit=2)]
        records = [event for event in events if isinstance(event, ToolCallsNotDispatched)]
        passed = not called and len(records) == 1 and len(records[0].calls) >= 1 and isinstance(events[-1], MessageDone) and events[-1].stop_reason == 'graph_budget'
        calls = [event for event in events if isinstance(event, ProviderCallCompleted)]
        passed = passed and len(calls) == 1
        print(json.dumps({'vendor': vendor.id, 'model': vendor.model_name, 'passed': passed, 'executed': bool(called),
            'provider_calls': len(calls), 'output_tokens': calls[0].output_tokens if calls else None}))
    except Exception:
        pytest.fail('真实预算未派发验证失败；不输出原始异常或响应', pytrace=False)
    assert passed
