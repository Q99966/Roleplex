"""确定性 fake provider。

普通回归测试和端到端测试都不允许依赖真实模型计费或不稳定输出，因此这里提供一个
脚本化的模型：它实现与真实 provider 相同的 `BaseChatModel` 接口（含 `bind_tools`），
因此可以走完全相同的 Agent 循环与防腐层路径，而不是绕开它们另开一条捷径。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field, PrivateAttr

# M2 起沿用的回复文案，端到端测试依赖其前缀，修改会导致浏览器断言失效。
FAKE_REPLY_TEMPLATE = "已收到你的消息：{prompt}\n\n这是 M2 fake provider 的确定性回复。"


@dataclass
class ScriptedTurn:
    """一次模型回合：产出文本，或发起一组工具调用。"""

    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


class ScriptedChatModel(BaseChatModel):
    """按脚本逐回合产出结果的确定性模型。

    回合用完后重复最后一回合，避免测试因多算一次模型调用而卡死。
    """

    turns: list[ScriptedTurn] = Field(default_factory=list)
    chunk_size: int = 6
    delay: float = 0.08
    index: int = 0

    @property
    def _llm_type(self) -> str:
        return "roleplex-scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedChatModel":
        """脚本模型不依赖工具 schema，直接返回自身以保持接口一致。"""
        return self

    def _next_turn(self) -> ScriptedTurn:
        """取出当前回合并推进游标。"""
        turn = self.turns[min(self.index, len(self.turns) - 1)]
        self.index += 1
        return turn

    def _generate(self, messages: list[Any], stop: Any = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
        turn = self._next_turn()
        message = AIMessage(content=turn.text, tool_calls=list(turn.tool_calls))
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _stream(self, messages: list[Any], stop: Any = None, run_manager: Any = None, **kwargs: Any) -> Iterator[ChatGenerationChunk]:
        turn = self._next_turn()
        for chunk in self._chunks(turn):
            yield chunk

    async def _astream(self, messages: list[Any], stop: Any = None, run_manager: Any = None, **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        """异步流式产出，用固定间隔模拟真实厂商的分片节奏。

        间隔让停止生成、断线恢复这类时序相关的测试有稳定的观察窗口。
        """
        turn = self._next_turn()
        for chunk in self._chunks(turn):
            await asyncio.sleep(self.delay)
            yield chunk

    def _chunks(self, turn: ScriptedTurn) -> list[ChatGenerationChunk]:
        """把一个回合展开为流式分片，同响应多工具保留各自索引。

        Args:
            turn：受控模型回合，不伪造厂商用量。
        """
        if turn.tool_calls:
            return [
                *[ChatGenerationChunk(message=AIMessageChunk(content=turn.text[i:i + self.chunk_size]))
                  for i in range(0, len(turn.text), self.chunk_size)],
                ChatGenerationChunk(
                    message=AIMessageChunk(
                        content="",
                        tool_call_chunks=[{
                            "name": call["name"],
                            "args": json.dumps(call.get("args", {}), ensure_ascii=False),
                            "id": call.get("id", f"call_{index + 1}"),
                            "index": index,
                        } for index, call in enumerate(turn.tool_calls)],
                    )
                )
            ]
        text = turn.text
        return [
            ChatGenerationChunk(message=AIMessageChunk(content=text[i:i + self.chunk_size]))
            for i in range(0, len(text), self.chunk_size)
        ]


class WorldCoordinatorProbeModel(ScriptedChatModel):
    """世界委派固件只使用真实概览的群/模板引用，并等待实际群结果。"""
    _groups: list = PrivateAttr(default_factory=list)

    async def _astream(self, messages, **kwargs):
        outputs = [m for m in messages if isinstance(m, ToolMessage)]
        def call(name, args):
            return ScriptedTurn(tool_calls=[{'name': name, 'args': args, 'id': f'world-probe-{self.index}'}])
        if self.index == 0:
            turn = call('world_read_overview', {})
        elif self.index in (1, 2):
            if self.index == 1:
                self._groups = json.loads(outputs[-1].content)['groups'][:2]
            group = self._groups[self.index - 1]
            turn = call('world_delegate_group', {'conversation_id': group['id'], 'definition_id': group['definition_id'],
                'expected_graph_revision': group['definition_revision'], 'goal': '执行既有受控流程并交付结果。',
                'request_key': f'group-{group["id"]}', 'mode': 'execute'})
        elif self.index == 3:
            turn = call('world_wait_task', {'seconds': 30})
        elif self.index == 4:
            turn = call('world_finish_task', {'summary': '两个群已完成真实流程。'})
        else:
            turn = ScriptedTurn(text='世界任务已依据两个群的真实执行结果完成。')
        self.index += 1
        for chunk in self._chunks(turn):
            yield chunk


class CommunicationWorldProbe(ScriptedChatModel):
    """只在受控验收中根据真实概览选择目标群，验证世界→群→角色的署名链。"""
    async def _astream(self, messages, **kwargs):
        outputs = [item for item in messages if isinstance(item, ToolMessage)]
        def call(name, args): return ScriptedTurn(tool_calls=[{'name': name, 'args': args, 'id': f'communication-{self.index}'}])
        if self.index == 0: turn = call('world_read_overview', {})
        elif self.index == 1:
            group = next(item for item in json.loads(outputs[-1].content)['groups'] if item['title'] == '协作署名验收')
            turn = call('world_delegate_group', {'conversation_id': group['id'], 'definition_id': group['definition_id'],
                'expected_graph_revision': group['definition_revision'], 'request_key': 'communication-group',
                'goal': 'WORLD_PUBLIC_TASK_TARGET 三个角色分别完成自己的分工。', 'mode': 'execute'})
        elif self.index == 2: turn = call('world_wait_task', {'seconds': 30})
        elif self.index == 3: turn = call('world_finish_task', {'summary': '三个角色已分别完成，来源和接收对象可核对。'})
        else: turn = ScriptedTurn(text='世界协调任务已完成。')
        self.index += 1
        for chunk in self._chunks(turn):
            await asyncio.sleep(self.delay)
            yield chunk


class WorldTypeProbeModel(ScriptedChatModel):
    """只从实际输入回报受控类型资料，验证初始化/构建器/模型的真实交接。"""
    async def _astream(self, messages, **kwargs):
        value = next((str(m.content) for m in messages if str(m.content).startswith('世界类型资料（')), '')
        if value:
            items = json.loads(value.split('\n', 1)[1])['items']
            value = ' '.join(item['text'] for item in items)
        for chunk in self._chunks(ScriptedTurn(text='世界类型资料核对：' + (value or '没有类型材料'))):
            yield chunk


class ContextCompactionModel(ScriptedChatModel):
    """压缩验收固件从实际传入的来源取简短句子和引用，不伪造 Provider usage。"""
    delay: float = 0
    chunk_size: int = 256

    async def _astream(self, messages, **kwargs):
        payload = json.loads(messages[-1].content)
        if '[COMPACT_SLOW]' in payload.get('instructions', ''):
            await asyncio.sleep(3)
        facts = []
        for item in payload['items']:
            if 'summary' in item:
                facts.extend(item['summary'].get('facts', []))
            else:
                facts.append({'text': item['text'].split('。')[0][:30], 'sources': item['sources'][:1]})
        facts = facts[:8]
        value = {'facts': facts, 'open_items': [], 'conflicts': [], 'inferences': []}
        while len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()) > payload['target_tokens'] and len(facts) > 1:
            facts.pop()
        self.turns = [ScriptedTurn(text=json.dumps(value, ensure_ascii=False, separators=(',', ':')))]
        self.index = 0
        async for chunk in super()._astream(messages, **kwargs):
            yield chunk


class MemoryProbeModel(ScriptedChatModel):
    """检索验收根据真实工具结果继续回读；校验内容只存在于受控来源，模型不预埋答案。"""
    query: str
    chunk_size: int = 256

    async def _astream(self, messages, **kwargs):
        replies = [message for message in messages if isinstance(message, ToolMessage)]
        if not replies:
            turn = ScriptedTurn(tool_calls=[{'name': 'memory_search', 'args': {'query': self.query, 'scope': 'related'}, 'id': 'memory-search'}])
        elif len(replies) == 1:
            try:
                results = json.loads(replies[-1].content)['results']
            except (ValueError, KeyError, TypeError):
                results = []
            turn = ScriptedTurn(tool_calls=[{'name': 'memory_read', 'args': {'reference': results[0]['reference']}, 'id': 'memory-read'}]) if results else ScriptedTurn(text='历史检索没有可共享结果。')
        else:
            try:
                content = json.loads(replies[-1].content)['text']
                text = '历史原文核对：' + content
            except (ValueError, KeyError, TypeError):
                text = '历史原文暂时无法读取。'
            turn = ScriptedTurn(text=text)
        self.turns, self.index = [turn], 0
        async for chunk in super()._astream(messages, **kwargs):
            yield chunk


class SearchReadModel(ScriptedChatModel):
    """T2 浏览器模型按真实搜索结果生成行读取请求，不预埋目标行号。"""
    _hit: dict = PrivateAttr(default_factory=dict)
    stale: bool = False

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        """按实际 ToolMessage 定位，后续仍交给真实工具执行。

        Args:
            messages：框架传入的本轮消息与真实工具结果。
            stop：模型接口停止条件。
            run_manager：框架回调。
            kwargs：兼容附加参数，不转发到外部服务。
        """
        if self.index == 0:
            turn = ScriptedTurn(tool_calls=[{'name': 'workspace_search', 'args': {'queries': ['TARGET_FUNCTION', 'SECOND_TARGET'], 'match': 'any'}, 'id': 't2-search'}])
        elif self.index == 1:
            outputs = [message for message in messages if isinstance(message, ToolMessage)]
            try:
                self._hit = json.loads(outputs[-1].content)['matches'][0]
                turn = ScriptedTurn(tool_calls=[{'name': 'workspace_read', 'args': {
                    'path': self._hit['path'], 'start_line': self._hit['line_number'], 'end_line': self._hit['line_number'] + 1,
                    'expected_sha256': '0' * 64 if self.stale else self._hit['sha256']}, 'id': 't2-range'}])
            except (ValueError, KeyError, IndexError):
                turn = ScriptedTurn(text='没有获得可用搜索定位结果。')
        elif self.index == 2 and self._hit and not self.stale:
            turn = ScriptedTurn(tool_calls=[{'name': 'workspace_read', 'args': {'items': [
                {'path': self._hit['path'], 'start_line': self._hit['line_number'], 'end_line': self._hit['line_number'] + 1},
                {'path': self._hit['path'], 'start_line': self._hit['line_number'] + 2, 'end_line': self._hit['line_number'] + 2},
                {'path': 'missing.txt'}, {'path': 'small.txt', 'max_bytes': 32768},
            ]}, 'id': 't2-batch'}])
        else:
            turn = ScriptedTurn(text='版本冲突已确认，未修改文件。' if self.stale else '搜索与范围读取完成。')
        self.index += 1
        for chunk in self._chunks(turn):
            await asyncio.sleep(self.delay)
            yield chunk


class ReplacementsModel(ScriptedChatModel):
    """根据实际读取的版本发起多片段编辑，失败场景不重试。"""
    invalid: bool = False

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        """Args:
            messages：包含实际读取结果的框架消息。
            stop：兼容模型接口。
            run_manager：框架回调。
            kwargs：附加接口参数。
        """
        if self.index == 0:
            turn = ScriptedTurn(tool_calls=[{'name':'workspace_read', 'args':{'path':'sample.txt'}, 'id':'replacement-read'}])
        elif self.index == 1:
            output = next(message for message in reversed(messages) if isinstance(message, ToolMessage))
            digest = json.loads(output.content)['sha256']
            pairs = [{'old_text': f'{key}={old}', 'new_text': f'{key}={new}'} for key,old,new in [('alpha',1,10),('beta',2,20),('gamma',3,30)]]
            if self.invalid:
                pairs = [{'old_text':'alpha=10','new_text':'alpha=11'},{'old_text':'missing-marker','new_text':'invalid'}]
            args = {'path':'sample.txt','expected_sha256':digest,'replacements':pairs}
            calls = [{'name':'workspace_edit','args':args,'id':'replacement-edit'}]
            if self.invalid: calls.append({'name':'workspace_edit','args':{'items':[args]},'id':'replacement-batch-error'})
            turn = ScriptedTurn(tool_calls=calls)
        else:
            turn = ScriptedTurn(text='多片段拒绝已确认。' if self.invalid else '多片段编辑完成。')
        self.index += 1
        for chunk in self._chunks(turn):
            await asyncio.sleep(self.delay)
            yield chunk


class GroupFileModel(ScriptedChatModel):
    """按本角色实际工具集选择写入或读取编辑，验证群聊租用交接。"""
    _writer: bool = PrivateAttr(default=False)
    _reader: bool = PrivateAttr(default=False)

    def bind_tools(self, tools, **kwargs):
        """仅观察宿主实际暴露工具，不为缺权限角色伪造成功。"""
        names = {tool.name for tool in tools}
        self._writer = 'workspace_write' in names
        self._reader = 'workspace_read' in names and 'workspace_edit' in names
        return self

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        """后续角色使用真实读取结果中的 hash 编辑前一角色创建的文件。"""
        if self._writer and self.index == 0:
            turn = ScriptedTurn(tool_calls=[{'name': 'workspace_write', 'args': {
                'path': 'group.txt', 'content': 'first'}, 'id': 'group-write'}])
        elif self._reader and self.index == 0:
            turn = ScriptedTurn(tool_calls=[{'name': 'workspace_read', 'args': {'path': 'group.txt'}, 'id': 'group-read'}])
        elif self._reader and self.index == 1:
            output = next(message for message in reversed(messages) if isinstance(message, ToolMessage))
            try:
                digest = json.loads(output.content)['sha256']
                turn = ScriptedTurn(tool_calls=[{'name': 'workspace_edit', 'args': {'path': 'group.txt',
                    'old_text': 'first', 'new_text': 'second', 'expected_sha256': digest}, 'id': 'group-edit'}])
            except (ValueError, KeyError):
                turn = ScriptedTurn(text='未获得可编辑版本。')
        else:
            turn = ScriptedTurn(text='群聊写入角色完成。' if self._writer else '群聊编辑角色完成。' if self._reader else '本角色未获文件工具授权。')
        self.index += 1
        for chunk in self._chunks(turn):
            await asyncio.sleep(self.delay)
            yield chunk


class FeedbackCoordinationModel(ScriptedChatModel):
    """通过真实图/反馈工具完成受控的局部处置和复核，用于隔离集成验收。"""
    prompt: str = ''
    _graph: dict = PrivateAttr(default_factory=dict)
    _feedback: dict = PrivateAttr(default_factory=dict)
    _role_id: int = PrivateAttr(default=0)
    _assigned: bool = PrivateAttr(default=False)

    async def _astream(self, messages, **kwargs):
        outputs = [m for m in messages if isinstance(m, ToolMessage)]
        last = {}
        if outputs:
            try: last = json.loads(outputs[-1].content)
            except (ValueError, TypeError): pass
        def call(name, args):
            return ScriptedTurn(tool_calls=[{'name': name, 'args': args, 'id': f'feedback-step-{self.index}'}])
        if self.index == 0:
            turn = call('workflow_read_graph', {})
        elif self.index == 1:
            self._graph = last
            turn = call('workflow_inspect_run', {})
        elif self.index == 2:
            self._feedback = next(item for item in last['feedback'] if item['status'] not in ('resolved', 'accepted', 'dismissed', 'obsolete'))
            item = self._feedback
            if item['category'] in ('capability', 'unverified') and item['status'] != 'review':
                turn = call('workflow_feedback_update', {'feedback_id': item['id'], 'expected_revision': item['revision'],
                    'request_key': 'wait-capability', 'action': 'wait', 'reason': '当前授权不能完成所需运行验证，等待 Owner 配置能力或人工验证。'})
            elif item['status'] == 'review':
                turn = call('workflow_feedback_update', {'feedback_id': item['id'], 'expected_revision': item['revision'],
                    'request_key': 'verify-feedback', 'action': 'resolve', 'reason': '已核对处理节点真实完成及结构化验证结果',
                    'verification_attempt_id': item['verification_attempt_id']})
            else:
                self._assigned = True
                self._role_id = next(r['role_id'] for r in self._graph['members'] if r['role_id'] != item['source_role_id'])
                graph = self._graph['graph']
                nid = 'feedback_fix_' + item['id'][:8]
                operations = [
                    {'op': 'add_node', 'node': {'id': nid, 'kind': 'role', 'title': '实现修复与验证' if item['category'] == 'implementation' else '契约裁定与验证',
                        'role_id': self._role_id, 'task': '[FEEDBACK_REPAIR]', 'tools': [], 'inputs': [item['node_id']],
                        'result_schema': {'feedback_resolved': 'boolean'}}},
                    {'op': 'connect', 'source': item['node_id'], 'target': nid},
                ]
                for source, target in graph['edges']:
                    if source == item['node_id']:
                        operations.append({'op': 'connect', 'source': nid, 'target': target})
                for loop in graph.get('loops', []):
                    if item['node_id'] in loop['body']:
                        operations.append({'op': 'upsert_loop', 'loop': {**loop, 'body': [*loop['body'], nid]}})
                turn = call('workflow_edit_graph', {'expected_graph_revision': self._graph['graph_revision'],
                    'mutation_key': 'feedback-local-repair', 'operations': operations})
        elif self.index == 3 and self._assigned:
            item = self._feedback
            turn = call('workflow_feedback_update', {'feedback_id': item['id'], 'expected_revision': item['revision'],
                'request_key': 'assign-feedback', 'action': 'assign', 'reason': '先裁定冲突并核对结果，再继续交付',
                'handler_role_id': self._role_id, 'handler_node_ids': ['feedback_fix_' + item['id'][:8]]})
        else:
            turn = ScriptedTurn(text='本次反馈处置已提交，后续状态以实际执行结果为准。')
        self.index += 1
        for chunk in self._chunks(turn):
            await asyncio.sleep(self.delay)
            yield chunk


class GraphControlModel(ScriptedChatModel):
    """图管理 E2E：从实际读图响应构造写入/编辑/启动调用，不直接播种最终定义。"""
    prompt: str = ''
    _read: dict = PrivateAttr(default_factory=dict)

    async def _astream(self,messages,stop=None,run_manager=None,**kwargs):
        meta=json.loads(self.prompt.split('本次图管理授权：',1)[1].split('\n',1)[0])
        if meta['mode'] == 'replan' and self.index == 2 and '[GRAPH_REPLAN_PAUSE]' in self.prompt:
            # 浏览器先观察真实图提交，再取消仍活跃的协调执行；等待可被任务取消中断。
            await asyncio.sleep(30)
        outputs=[m for m in messages if isinstance(m,ToolMessage)]
        last={}
        if outputs:
            try: last=json.loads(outputs[-1].content)
            except (ValueError,TypeError): pass
        def call(name,args): return ScriptedTurn(tool_calls=[{'name':name,'args':args,'id':f'graph-step-{self.index}'}])
        if self.index==0:
            turn=call('workflow_read_graph',{})
        elif self.index==1:
            self._read=last
            if meta['mode']=='replan':
                graph=last.get('graph',{})
                sources=[n for n in graph.get('nodes',[]) if n['kind']=='role']
                source=sources[0]['id'] if sources else graph['nodes'][0]['id']
                role=next(r for r in last['members'] if 'workspace_read' in r['tools'])
                turn=call('workflow_edit_graph',{'expected_graph_revision':last['graph_revision'],'mutation_key':'add-runtime-review',
                    'operations':[{'op':'add_node','node':{'id':'runtime_review','kind':'role','title':'运行新增审查','role_id':role['role_id'],
                        'task':'[WF_REVIEW]','tools':['workspace_read'],'inputs':[source],'result_schema':{'approved':'boolean'}}},
                        {'op':'connect','source':source,'target':'runtime_review'}]})
            elif '[GRAPH_CREATE]' in self.prompt or not last.get('graph',{}).get('nodes'):
                turn=call('workflow_write_graph',{'expected_graph_revision':last['graph_revision'],'mutation_key':'create-target-graph',
                    'name':'协调者生成的流程','graph':{'runtime_version':2,'nodes':[{'id':'gate','kind':'approval','title':'开始前确认','position':{'x':0,'y':100}}],'edges':[]}})
            else:
                # 兼容既有预设图验收入口：读取后用局部操作确认并发配置，再显式启动。
                turn=call('workflow_edit_graph',{'expected_graph_revision':last['graph_revision'],'mutation_key':'confirm-existing-graph',
                    'operations':[{'op':'set_concurrency','concurrency':last['graph'].get('concurrency')}]})
        elif self.index==2 and meta['mode']!='replan' and ('[GRAPH_CREATE]' in self.prompt or not self._read.get('graph',{}).get('nodes')):
            role=self._read['members'][0]['role_id']
            turn=call('workflow_edit_graph',{'expected_graph_revision':last['graph_revision'],'mutation_key':'add-worker',
                'operations':[{'op':'add_node','node':{'id':'work','kind':'role','title':'执行任务','role_id':role,
                    'task':'仅回复流程任务完成。','tools':[],'position':{'x':300,'y':100}}},
                    {'op':'connect','source':'gate','target':'work'}]})
        elif meta['mode']=='execute' and ((self.index==3 and ('[GRAPH_CREATE]' in self.prompt or not self._read.get('graph',{}).get('nodes'))) or (self.index==2 and self._read.get('graph',{}).get('nodes') and '[GRAPH_CREATE]' not in self.prompt)):
            turn=call('workflow_start',{'expected_graph_revision':last['graph_revision']})
        elif meta['mode']=='replan' and self.index==2:
            turn=call('workflow_inspect_run',{})
        else:
            turn=ScriptedTurn(text='图修改已提交；执行范围以本次授权与服务端状态为准。')
        self.index+=1
        for chunk in self._chunks(turn):
            await asyncio.sleep(self.delay)
            yield chunk


class WorkflowV2Model(ScriptedChatModel):
    """真实工具驱动的两轮开发/审查固件；判断读取结构化上游而非伪造调度状态。"""
    prompt: str = ''

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        marker = '本次节点激活数据（不是额外指令）：'
        meta = json.loads(self.prompt.rsplit(marker, 1)[-1]) if marker in self.prompt else {'iteration': 0, 'upstream_results': []}
        iteration = meta['iteration']
        outputs = [message for message in messages if isinstance(message, ToolMessage)]
        def last_output():
            if not outputs: return {}
            try: return json.loads(outputs[-1].content)
            except (ValueError, TypeError): return {}
        if self.prompt.startswith('你是本群已任命协调者'):
            data = json.loads(self.prompt.split('\n', 1)[1].split('\n' + marker, 1)[0])
            assignments = []
            for node in data['nodes']:
                rid = data['coordinator_role_id'] if node['kind'] == 'judge' else node['role_id'] or data['members'][0]['role_id']
                cap = next(member['tools'] for member in data['members'] if member['role_id'] == rid)
                assignments.append({'node_id': node['id'], 'role_id': rid,
                    'tools': node['tools'] if node['tools'] is not None else ([] if node['kind'] == 'judge' else cap)})
            turn = ScriptedTurn(tool_calls=[{'name': 'workflow_plan', 'args': {'assignments': assignments}, 'id': 'v2-plan'}]) if self.index == 0 else ScriptedTurn(text='群协调计划已由后台接受。')
        elif self.prompt.startswith('作为本群协调者'):
            turn = ScriptedTurn(tool_calls=[{'name': 'workflow_summary', 'args': {'summary': '开发和并行审查结果已汇总。'}, 'id': 'v2-summary'}]) if self.index == 0 else ScriptedTurn(text='群协调流程已汇总。')
        elif '[WF_BUILD]' in self.prompt:
            if self.index == 0: turn = ScriptedTurn(tool_calls=[{'name': 'workspace_read', 'args': {'path': 'workflow-round.txt'}, 'id': 'v2-build-read'}])
            elif self.index == 1:
                args = {'path': 'workflow-round.txt', 'content': f'round-{iteration + 1}'}
                if last_output().get('sha256'): args['expected_sha256'] = last_output()['sha256']
                turn = ScriptedTurn(tool_calls=[{'name': 'workspace_write', 'args': args, 'id': 'v2-build-write'}])
            elif self.index == 2: turn = ScriptedTurn(tool_calls=[{'name': 'workflow_result', 'args': {'values': {'round': iteration + 1}}, 'id': 'v2-build-report'}])
            else: turn = ScriptedTurn(text=f'开发第 {iteration + 1} 轮结束。')
        elif '[WF_REVIEW]' in self.prompt:
            if self.index == 0: turn = ScriptedTurn(tool_calls=[{'name': 'workspace_read', 'args': {'path': 'workflow-round.txt'}, 'id': 'v2-review-read'}])
            elif self.index == 1:
                observed = last_output().get('text', '')
                turn = ScriptedTurn(tool_calls=[{'name': 'workflow_result', 'args': {'values': {'approved': observed == 'round-2', 'observed': observed}}, 'id': 'v2-review-report'}])
            else: turn = ScriptedTurn(text=f'审查第 {iteration + 1} 轮结束。')
        else:
            reports = [r['result']['values']['approved'] for r in meta['upstream_results'] if 'approved' in (r.get('result') or {}).get('values', {})]
            turn = ScriptedTurn(tool_calls=[{'name': 'workflow_result', 'args': {'values': {'approved': bool(reports) and all(reports)}}, 'id': 'v2-judge'}]) if self.index == 0 else ScriptedTurn(text=f'本轮结构化判断完成：{iteration + 1}。')
        self.index += 1
        for chunk in self._chunks(turn):
            await asyncio.sleep(self.delay)
            yield chunk


class PromptLayersProbeModel(ScriptedChatModel):
    """只回报受控验收标记，证明界面保存的规则确实到达实际模型输入。"""

    async def _astream(self, messages, **kwargs):
        import re
        system = next((str(message.content) for message in messages if message.type == 'system'), '')
        markers = list(dict.fromkeys(re.findall(r'PROMPT_TEST_(?:PLATFORM|WORLD|ROLE|CONVERSATION)_[A-Z0-9]+', system)))
        for chunk in self._chunks(ScriptedTurn(text='提示词验证：' + ','.join(markers))):
            await asyncio.sleep(self.delay)
            yield chunk


def fake_reply_model(prompt: str, *, delay: float = 0.08) -> ScriptedChatModel:
    """构造只产出固定回复文案的 fake 模型。

    Args:
        prompt：用户当前消息文本，会被拼进回复以便断言输入确实到达了模型。
        delay：分片间隔秒数。
    """
    if '[COMMUNICATOR_WORLD_PROBE]' in prompt:
        return CommunicationWorldProbe(delay=0)
    if '[COMMUNICATOR_WORK_PROBE]' in prompt:
        return ScriptedChatModel(delay=0, turns=[ScriptedTurn(tool_calls=[{'name': 'workflow_result', 'args': {
            'values': {'completed': True}, 'summary': '本角色的受控任务已完成。'}, 'id': 'communication-result'}]), ScriptedTurn(text='本角色已完成自己的分工。')])
    if '[WORLD_TYPE_TOOL_PROBE]' in prompt:
        return ScriptedChatModel(delay=0, turns=[ScriptedTurn(tool_calls=[{'name': 'type_fixture_record', 'args': {}, 'id': 'type-record'}]), ScriptedTurn(text='类型工具已完成真实记录。')])
    if '[WORLD_ACTIVITY_PROBE]' in prompt:
        return ScriptedChatModel(delay=0, turns=[ScriptedTurn(tool_calls=[{'name': 'world_activity_fixture_record', 'args': {'request_key': 'fixture-activity'}, 'id': 'start-type'}]),
            ScriptedTurn(tool_calls=[{'name': 'world_wait_task', 'args': {'seconds': 30}, 'id': 'wait-type'}]),
            ScriptedTurn(tool_calls=[{'name': 'world_finish_task', 'args': {'summary': '类型活动已实际执行。'}, 'id': 'finish-type'}]), ScriptedTurn(text='类型活动完成。')])
    if '[WORLD_TASK_PROBE]' in prompt:
        return WorldCoordinatorProbeModel(delay=0)
    if '[WORLD_TYPE_PROBE]' in prompt:
        return WorldTypeProbeModel(delay=0)
    if '[MEMORY_PROBE]' in prompt:
        return MemoryProbeModel(query=prompt.split('[MEMORY_PROBE]', 1)[1].strip(), delay=delay)
    if '[PROMPT_LAYERS_PROBE]' in prompt:
        return PromptLayersProbeModel(delay=0)
    if '本次图管理授权：' in prompt:
        meta = json.loads(prompt.split('本次图管理授权：', 1)[1].split('\n', 1)[0])
        if meta.get('feedback_ids'):
            return FeedbackCoordinationModel(prompt=prompt, delay=delay)
        return GraphControlModel(prompt=prompt,delay=delay)
    if any(marker in prompt for marker in ['[WF_BUILD]', '[WF_REVIEW]', '[WF_JUDGE]', '你是本群已任命协调者', '作为本群协调者']):
        return WorkflowV2Model(prompt=prompt, delay=delay)
    if any(marker in prompt for marker in ('[FEEDBACK_REPORT]', '[FEEDBACK_REPAIR]', '[FEEDBACK_IMPLEMENTATION]', '[FEEDBACK_CAPABILITY]', '[FEEDBACK_UNVERIFIED]')):
        repair = '[FEEDBACK_REPAIR]' in prompt
        category = 'implementation' if '[FEEDBACK_IMPLEMENTATION]' in prompt else 'capability' if '[FEEDBACK_CAPABILITY]' in prompt else 'unverified' if '[FEEDBACK_UNVERIFIED]' in prompt else 'contract'
        args = {'values': {'feedback_resolved': True}, 'summary': '受控契约裁定与验证完成'} if repair else {
            'values': {'checked': True}, 'summary': '受控审查发现契约冲突',
            'feedback': [{'request_key': 'feedback-one', 'category': category,
                          'summary': {'contract': '两个验收数字冲突', 'implementation': '实现结果需要修正', 'capability': '缺少运行验证工具', 'unverified': '尚未完成运行验证'}[category],
                          'details': '需要责任角色处理并核对。', 'blocking': True,
                          'requested_tools': ['workspace_run_shell'] if category == 'capability' else []}],
        }
        return ScriptedChatModel(delay=delay, turns=[ScriptedTurn(tool_calls=[
            {'name': 'workflow_result', 'args': args, 'id': 'feedback-report'}]), ScriptedTurn(text='受控节点报告已提交。')])
    if '[GROUP_FILES_FAKE]' in prompt:
        return GroupFileModel(delay=delay)
    if '[REPLACEMENTS_FAKE]' in prompt or '[REPLACEMENTS_BAD_FAKE]' in prompt:
        return ReplacementsModel(delay=delay, invalid='[REPLACEMENTS_BAD_FAKE]' in prompt)
    if '[SEARCH_READ_FAKE]' in prompt or '[SEARCH_STALE_FAKE]' in prompt:
        return SearchReadModel(delay=delay, stale='[SEARCH_STALE_FAKE]' in prompt)
    if '[TOOL_ID_ERROR_FAKE]' in prompt:
        return ScriptedChatModel(delay=delay, turns=[ScriptedTurn(tool_calls=[{'name':'workspace_write','args':{'path':'recovered.txt','content':'must-not-write'},'id':'duplicate'}] * 2)])
    if '[ARGUMENT_RECOVERY_FAKE]' in prompt or '[JSON_RECOVERY_FAKE]' in prompt:
        return ArgumentRecoveryModel(delay=delay, broken_json='[JSON_RECOVERY_FAKE]' in prompt, turns=[
            ScriptedTurn(tool_calls=[{'name':'workspace_write','args':{'path':'recovered.txt','content':123},'id':'bad-schema'}]),
            ScriptedTurn(tool_calls=[{'name':'workspace_write','args':{'path':'recovered.txt','content':'confirmed'},'id':'corrected'}]),
            ScriptedTurn(text='参数修正后已完成。')])
    if '[INTERRUPTION_SETUP_FAKE]' in prompt:
        return ScriptedChatModel(turns=[ScriptedTurn(tool_calls=[{'name':'workspace_write','id':'interruption-create',
            'args':{'path':'interruption.txt','content':'alpha=1\n'}}]),ScriptedTurn(text='等待用户停止。'*300)],delay=delay)
    if '请将已写好的alpha改为2' in prompt:
        return InterruptionAwareModel(delay=delay)
    if '[BATCH_UNLIMITED_FAKE]' in prompt:
        # 真实浏览器链路验证大批次、单文件超过旧批次额度，以及后续全部编辑。
        contents=['old\n'+'中'*100000]+['old']*59
        return ScriptedChatModel(turns=[
            ScriptedTurn(tool_calls=[{'name':'workspace_write','id':'large-create','args':{'items':[
                {'path':f'file-{i:02d}.txt','content':content} for i,content in enumerate(contents)]}}]),
            ScriptedTurn(tool_calls=[{'name':'workspace_edit','id':'large-edit','args':{'items':[
                {'path':f'file-{i:02d}.txt','old_text':'old','new_text':'new',
                 'expected_sha256':hashlib.sha256(content.encode()).hexdigest()} for i,content in enumerate(contents)]}}]),
            ScriptedTurn(text='大批次修改完成。')],delay=delay)
    if '[LONG_DECISIONS_FAKE]' in prompt:
        return ScriptedChatModel(turns=[*[ScriptedTurn(tool_calls=[{'name': 'workspace_list', 'args': {}, 'id': f'long-{index}'}])
            for index in range(10)], ScriptedTurn(text='长任务完成。')], delay=delay)
    if '[BUDGET_PROPOSALS_FAKE]' in prompt or '[BUDGET_FINAL_FAKE]' in prompt:
        # 第八次决策可完成回答或提交最后两项，之后不得请求第九次决策。
        final = ScriptedTurn(text='预算边界任务完成。') if '[BUDGET_FINAL_FAKE]' in prompt else ScriptedTurn(tool_calls=[
            {'name': 'workspace_write', 'id': f'last-{index}',
             'args': {'path': f'last-{index}.txt', 'content': 'confirmed'}} for index in range(2)])
        return ScriptedChatModel(turns=[*[ScriptedTurn(tool_calls=[{'name': 'workspace_write', 'id': f'budget-{index}',
            'args': {'path': f'created-{index}.txt', 'content': 'confirmed'}}]) for index in range(7)], final], delay=delay)
    if '[EXECUTION_FACTS_FAKE]' in prompt:
        # 默认决策预算内先真实写入，再持续只读以确定性触顶。
        return ScriptedChatModel(turns=[ScriptedTurn(tool_calls=[{
            'name': 'workspace_write', 'args': {'path': 'facts-proof.txt', 'content': 'controlled-facts'}, 'id': 'facts-write',
        }]), *[ScriptedTurn(tool_calls=[{'name': 'workspace_list', 'args': {}, 'id': f'facts-list-{index}'}])
               for index in range(10)]], delay=delay)
    if '[WRITE_DIAGNOSTIC_FAKE]' in prompt:
        return ScriptedChatModel(turns=[ScriptedTurn(text='尝试写入受控文件。', tool_calls=[{
            'name': 'workspace_write', 'args': {'path': 'blocked.txt', 'content': 'new'}, 'id': 'diagnostic_write'}]),
            ScriptedTurn(text='再检查批次编辑拒绝。', tool_calls=[{'name': 'workspace_edit', 'args': {'items': [
                {'path': name, 'old_text': 'old', 'new_text': 'new', 'expected_sha256': hashlib.sha256(b'old').hexdigest()}
                for name in ['existing.txt', 'unstarted.txt']]}, 'id': 'diagnostic_batch'}]),
            ScriptedTurn(text='写入拒绝诊断流程结束。')], delay=delay)
    if '[SERVICE_DISCOVERY_FAKE]' in prompt:
        # 固定脚本只请求列表，不含任何启动返回的 runtime_id；通过真实工具取登记事实。
        return ScriptedChatModel(turns=[ScriptedTurn(tool_calls=[{
            'name': 'workspace_service_status', 'args': {}, 'id': 'service_discovery'}]),
            ScriptedTurn(text='当前会话服务查询完成。')], delay=delay)
    if '[SERVICE_FAKE:' in prompt:
        import re
        import shlex
        import sys
        match = re.search(r'\[SERVICE_FAKE:(\d+)\]', prompt)
        if match:
            port = int(match[1])
            script = f'{shlex.quote(sys.executable)} -u -m http.server {port} --bind 127.0.0.1'
            return ScriptedChatModel(turns=[ScriptedTurn(tool_calls=[{'name': 'workspace_start_service', 'args': {
                'script': script, 'port': port, 'lifetime_seconds': 60}, 'id': 'service_start'}]),
                ScriptedTurn(text='服务启动流程已结束，请使用 /ps 管理。')], delay=delay)
    if '[SHELL_WRITE_WAIT_FAKE]' in prompt:
        return ScriptedChatModel(turns=[ScriptedTurn(tool_calls=[
            {'name': 'workspace_run_shell', 'args': {'script': 'echo approval-placeholder'}, 'id': 'wait-shell'},
            {'name': 'workspace_write', 'args': {'items': [{'path': 'pending/proof.txt', 'content': 'written-before-approval'}]}, 'id': 'wait-write'},
        ]), ScriptedTurn(text='审批与写入验证完成。')], delay=delay)
    if any(marker in prompt for marker in ('[SHELL_APPROVAL_FAKE]', '[SHELL_EXPIRY_FAKE]', '[SHELL_DETAILS_FAKE]')):
        import os
        script = "[IO.File]::AppendAllText('shell-proof.txt', \"approved`n\")" if os.name == 'nt' else "printf 'approved\\n' >> shell-proof.txt"
        if '[SHELL_EXPIRY_FAKE]' in prompt:
            script = 'echo approval-expiry-placeholder'
        if '[SHELL_DETAILS_FAKE]' in prompt:
            script = ("[Console]::WriteLine('shell-stdout-placeholder'); [Console]::Error.WriteLine('shell-stderr-placeholder')"
                if os.name == 'nt' else "printf 'shell-stdout-placeholder\\n'; printf 'shell-stderr-placeholder\\n' >&2")
        return ScriptedChatModel(turns=[ScriptedTurn(tool_calls=[{'name': 'workspace_run_shell', 'args': {'script': script}, 'id': 'shell_approval'}]),
            ScriptedTurn(text='审批流程已结束。')], delay=delay)
    if '[TOOL_TIMELINE_FAKE]' in prompt:
        return ScriptedChatModel(turns=[
            ScriptedTurn(text='先读取😀。', tool_calls=[{
                'name': 'workspace_run_command', 'args': {'command': 'read', 'args': {'path': 'hello.txt'}}, 'id': 'timeline_read',
            }]),
            ScriptedTurn(text='读取完成，再统计。', tool_calls=[{
                'name': 'workspace_run_command', 'args': {'command': 'count', 'args': {'path': 'hello.txt'}}, 'id': 'timeline_count',
            }]),
            ScriptedTurn(text='处理完成。'),
        ], delay=delay)
    if '[BATCH_MUTATION_FAKE]' in prompt:
        files = [('src/nested/batch-a.txt', 'alpha old'), ('test/nested/batch-b.txt', 'beta old')]
        return ScriptedChatModel(turns=[ScriptedTurn(text='先创建两个文件。', tool_calls=[{
            'name': 'workspace_write', 'args': {'items': [{'path': name, 'content': text} for name, text in files]}, 'id': 'batch_create'}]),
            ScriptedTurn(text='再分别局部修改。', tool_calls=[{'name': 'workspace_edit', 'args': {'items': [
                {'path': name, 'old_text': 'old', 'new_text': 'new', 'expected_sha256': hashlib.sha256(text.encode()).hexdigest()}
                for name, text in files]}, 'id': 'batch_edit'}]), ScriptedTurn(text='批量修改验收完成。')], delay=delay)
    if '[READ_MANY_FAKE]' in prompt:
        return ScriptedChatModel(turns=[ScriptedTurn(text='批量读取受控文件。', tool_calls=[{
            'name': 'workspace_read', 'args': {'items': [
                {'path': 'first.txt'}, {'path': 'missing.txt'}, {'path': 'second.txt'},
            ]}, 'id': 'read_many'}]), ScriptedTurn(text='再检查单文件兼容形式。', tool_calls=[{
                'name': 'workspace_read', 'args': {'path': 'second.txt'}, 'id': 'read_legacy'}]),
            ScriptedTurn(text='批量读取验收完成。')], delay=delay)
    if '[EXPLORE_FAKE]' in prompt:
        # 连续只读、缺失文件和正文断点，走真实工具与消息事件以验证纯展示归组。
        return ScriptedChatModel(turns=[
            ScriptedTurn(tool_calls=[{'name': name, 'args': {'path': path}, 'id': f'explore_{index}'}])
            for index, (name, path) in enumerate([
                ('workspace_read', 'explore.txt'), ('workspace_list', '.'),
                ('workspace_read', 'explore.txt'), ('workspace_read', 'missing.txt'),
            ])
        ] + [ScriptedTurn(text='第一段探索完成，下面单独核对目录。',
            tool_calls=[{'name': 'workspace_list', 'args': {'path': '.'}, 'id': 'explore_tail'}]),
            ScriptedTurn(text='探索验收完成。')], delay=max(delay, 0.4))
    if '[EDIT_FAKE' in prompt:
        first = "export const title = '第一版';\nexport const keep = '保持';\n"
        old = 'missing' if '[EDIT_FAKE_MISSING]' in prompt else '第一版'
        return ScriptedChatModel(turns=[
            ScriptedTurn(text='先读取当前文件。', tool_calls=[{'name': 'workspace_read', 'args': {'path': 'edit.ts'}, 'id': 'edit_read'}]),
            ScriptedTurn(text='只修改标题。', tool_calls=[{'name': 'workspace_edit', 'args': {'path': 'edit.ts', 'old_text': old,
                'new_text': '第二版🙂', 'expected_sha256': hashlib.sha256(first.encode()).hexdigest()}, 'id': 'edit_apply'}]),
            ScriptedTurn(text='编辑流程已结束。'),
        ], delay=delay)
    if '[WRITE_DIFF_FAKE]' in prompt:
        first = "export const title = '第一版';\nexport const keep = '保持';\n"
        after = "export const title = '第二版🙂';\nexport const keep = '保持';\n"
        return ScriptedChatModel(turns=[
            ScriptedTurn(text='先创建文件。', tool_calls=[{'name': 'workspace_write', 'args': {'path': 'demo.ts', 'content': first}, 'id': 'diff_create'}]),
            ScriptedTurn(text='现在修改标题。', tool_calls=[{'name': 'workspace_write', 'args': {
                'path': 'demo.ts', 'content': after, 'expected_sha256': hashlib.sha256(first.encode()).hexdigest()}, 'id': 'diff_update'}]),
            ScriptedTurn(text='修改完成。'),
        ], delay=delay)
    if "[W1A_FAKE_E2E]" in prompt:
        first = "W1a 第一版"
        first_hash = hashlib.sha256(first.encode("utf-8")).hexdigest()
        return ScriptedChatModel(
            turns=[
                ScriptedTurn(tool_calls=[{
                    "name": "workspace_list", "args": {"path": ".", "limit": 200}, "id": "w1a_list",
                }]),
                ScriptedTurn(tool_calls=[{
                    "name": "workspace_write",
                    "args": {"path": "hello.txt", "content": first, "expected_sha256": None},
                    "id": "w1a_create",
                }]),
                ScriptedTurn(tool_calls=[{
                    "name": "workspace_read", "args": {"path": "hello.txt"}, "id": "w1a_read_first",
                }]),
                ScriptedTurn(tool_calls=[{
                    "name": "workspace_write",
                    "args": {"path": "hello.txt", "content": "W1a 第二版", "expected_sha256": first_hash},
                    "id": "w1a_update",
                }]),
                ScriptedTurn(tool_calls=[{
                    "name": "workspace_read", "args": {"path": "hello.txt"}, "id": "w1a_read_final",
                }]),
                ScriptedTurn(text="W1a 工作区工具闭环完成。"),
            ],
            delay=delay,
        )
    if '[W1B_FAKE_E2E]' in prompt:
        commands = [('pwd', {}), ('list', {}), ('read', {'path': 'hello.txt'}), ('count', {'path': 'hello.txt'})]
        return ScriptedChatModel(turns=[
            *[ScriptedTurn(tool_calls=[{
                'name': 'workspace_run_command', 'args': {'command': command, 'args': args}, 'id': f'w1b_{command}',
            }]) for command, args in commands],
            ScriptedTurn(text='W1b 结构化命令流程结束。'),
        ], delay=delay)
    for marker, path in [
        ('[W1B_DENIED]', '../outside'), ('[W1B_OUTPUT]', 'output.txt'),
        ('[W1B_TIMEOUT]', 'timeout.txt'), ('[W1B_CANCEL]', 'cancel.txt'),
        ('[W1B_EXIT]', 'exit.txt'),
    ]:
        if marker in prompt:
            return ScriptedChatModel(turns=[
                ScriptedTurn(tool_calls=[{
                    'name': 'workspace_run_command', 'args': {'command': 'read', 'args': {'path': path}}, 'id': 'w1b_read',
                }]), ScriptedTurn(text='W1b 命令场景结束。'),
            ], delay=delay)
    return ScriptedChatModel(turns=[ScriptedTurn(text=FAKE_REPLY_TEMPLATE.format(prompt=prompt))], delay=delay)


class ArgumentRecoveryModel(ScriptedChatModel):
    """一次字段/JSON 错误后按工具反馈重发正确请求的确定性替身。"""
    broken_json: bool = False

    def _chunks(self, turn):
        """Args:
            turn：保留真实 SDK 消息分片解析路径的受控输出。
        """
        if self.broken_json and self.index == 1:
            return [ChatGenerationChunk(message=AIMessageChunk(content='',tool_call_chunks=[{
                'name':'workspace_write','args':'{"path":"recovered.txt","content":not-json}', 'id':'bad-json','index':0}]))]
        return super()._chunks(turn)


class InterruptionAwareModel(ScriptedChatModel):
    """仅从实际上下文证据取版本，验证非继续关键词也能收到交接。"""

    async def _astream(self,messages,stop=None,run_manager=None,**kwargs):
        """Args:
            messages：真实ContextBuilder与工具循环传入的历史。
            stop：框架停止条件。
            run_manager：框架回调。
            kwargs：其他框架参数。
        """
        if self.index==0:
            found=None
            for message in messages:
                content=getattr(message,'content','')
                if isinstance(content,str) and content.startswith('以下是服务器观察到的最近中断执行数据'):
                    value=json.loads(content.split('\n',1)[1])
                    found=next((fact for fact in value['facts'] if fact.get('path')=='interruption.txt' and fact.get('current')=='matches_recorded_version'),None)
            turn=ScriptedTurn(tool_calls=[{'name':'workspace_edit','id':'resume-edit','args':{
                'path':'interruption.txt','old_text':'alpha=1','new_text':'alpha=2','expected_sha256':found['observed_sha256']}}]) if found else ScriptedTurn(text='缺少可信执行事实。')
        else:turn=ScriptedTurn(text='已根据执行事实完成新要求。')
        self.index+=1
        for chunk in self._chunks(turn):
            await asyncio.sleep(self.delay)
            yield chunk
