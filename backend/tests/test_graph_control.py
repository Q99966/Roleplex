"""图管理的真实 API/模型工具路径；并发与授权使用隔离数据库。"""
import asyncio
import json
from uuid import uuid4
import pytest
from test_orchestrator import setup_group, command_root, isolated_command_database


def approval(nid='first'):
    return {'id':nid,'kind':'approval','title':nid}


async def write(client,headers,cid,did,graph,version=0,key=None,**kwargs):
    return await client.post(f'/api/conversations/{cid}/workflows/graphs/definition/{did}/write',headers=headers,
        json={'graph':graph,'expected_graph_revision':version,'mutation_key':key or uuid4().hex,**kwargs})


@pytest.mark.anyio
async def test_graph_write_edit_atomic_versions_and_layout(command_root,isolated_command_database):
    async with setup_group(command_root) as (client,headers,cid,ids,_):
        did=uuid4().hex
        initial={'nodes':[{**approval(),'position':{'x':100,'y':200},'color':'#123456'}],'edges':[]}
        key=uuid4().hex
        saved=await write(client,headers,cid,did,initial,key=key)
        assert saved.status_code==200,saved.text
        assert saved.json()['graph_revision']==1
        replay=await write(client,headers,cid,did,initial,key=key)
        assert replay.status_code==200 and replay.json()['replayed']
        assert replay.json()['name']==saved.json()['name']
        conflict=await write(client,headers,cid,did,{'nodes':[],'edges':[]},key=key)
        assert conflict.status_code==409
        base=f'/api/conversations/{cid}/workflows/graphs/definition/{did}'
        invalid=await client.post(base+'/edit',headers=headers,json={'expected_graph_revision':1,'mutation_key':uuid4().hex,
            'operations':[{'op':'add_node','node':approval('second')},{'op':'connect','source':'second','target':'missing'}]})
        assert invalid.status_code==422
        current=(await client.get(base,headers=headers)).json()
        assert current['graph_revision']==1 and len(current['graph']['nodes'])==1
        replacement=await write(client,headers,cid,did,{'nodes':[approval(),approval('second')],'edges':[['first','second']]},version=1)
        assert replacement.status_code==200
        assert replacement.json()['graph']['nodes'][0]['position']=={'x':100,'y':200}
        assert replacement.json()['graph']['nodes'][0]['color']=='#123456'
        dry=await client.post(base+'/edit',headers=headers,json={'expected_graph_revision':2,'mutation_key':uuid4().hex,
            'validate_only':True,'operations':[{'op':'update_node','node_id':'second','changes':{'title':'修改'}}]})
        assert dry.status_code==200 and not dry.json()['committed']
        current=(await client.get(base,headers=headers)).json()
        assert current['graph_revision']==2
        old=(await client.get(base+'?graph_revision=1',headers=headers)).json()
        assert len(old['graph']['nodes'])==1
        a,b=await asyncio.gather(*[client.post(base+'/edit',headers=headers,json={'expected_graph_revision':2,'mutation_key':uuid4().hex,
            'operations':[{'op':'update_node','node_id':'second','changes':{'title':title}}]}) for title in ['A','B']])
        assert sorted([a.status_code,b.status_code])==[200,409]


@pytest.mark.anyio
async def test_planning_without_run_gets_explicit_graph_tools(command_root,isolated_command_database,monkeypatch):
    from app.agent.fake_provider import ScriptedChatModel,ScriptedTurn
    from app.services import chat
    bound=[]
    class Model(ScriptedChatModel):
        def bind_tools(self,tools,**kwargs):
            bound.append({t.name for t in tools});return self
    turns=[ScriptedTurn(tool_calls=[{'name':'workflow_read_graph','args':{},'id':'read'}]),
        ScriptedTurn(tool_calls=[{'name':'workflow_write_graph','args':{'expected_graph_revision':0,'mutation_key':'create',
            'graph':{'nodes':[approval()],'edges':[]}},'id':'write'}]),
        ScriptedTurn(tool_calls=[{'name':'workflow_edit_graph','args':{'expected_graph_revision':1,'mutation_key':'edit',
            'operations':[{'op':'add_node','node':approval('second')},{'op':'connect','source':'first','target':'second'}]},'id':'edit'}]),
        ScriptedTurn(text='草稿已创建并增加节点，尚未执行。')]
    monkeypatch.setattr(chat,'fake_reply_model',lambda prompt:Model(turns=turns,delay=0))
    async with setup_group(command_root) as (client,headers,cid,ids,_):
        await client.put(f'/api/conversations/{cid}/orchestrator',headers=headers,json={'role_id':ids[3],'expected_revision':0})
        response=await client.post(f'/api/conversations/{cid}/workflows/coordination',headers=headers,
            json={'role_id':ids[3],'goal':'从目标规划两步流程','mode':'design','request_key':uuid4().hex})
        assert response.status_code==202,response.text
        grant=response.json()
        for _ in range(400):
            listing=(await client.get(f'/api/conversations/{cid}/workflows',headers=headers)).json()
            current=next(g for g in listing['coordinations'] if g['id']==grant['id'])
            if current['status'] not in ['queued','running','stopping']:break
            await asyncio.sleep(.025)
        assert current['status']=='completed',current['error_code']
        assert bound and bound[0]=={'workflow_read_graph','workflow_write_graph','workflow_edit_graph'}
        assert not listing['runs']
        assert listing['definitions'][0]['revision']==2
        assert len(listing['definitions'][0]['graph']['nodes'])==2
        assert current['used_decisions']==4
        from app.workflows.graph_tools import invoke
        late=await invoke(grant['execution_id'],'workflow_edit_graph',{'expected_graph_revision':2,'mutation_key':'late',
            'operations':[{'op':'remove_node','node_id':'second'}]})
        assert 'WORKFLOW_COORDINATION_REVOKED' in late


async def run_state(client,headers,cid,rid,done):
    for _ in range(900):
        value=(await client.get(f'/api/conversations/{cid}/workflows',headers=headers)).json()
        run=next(r for r in value['runs'] if r['id']==rid)
        if done(run): return run
        await asyncio.sleep(.025)
    raise AssertionError(('运行没有到达预期状态',run['status'],[(a['node_id'],a['status'],a['error_code']) for a in run['attempts']]))


@pytest.mark.anyio
async def test_replan_add_review_preserves_completed_and_waiting(command_root,isolated_command_database):
    from test_orchestrator import launch
    async with setup_group(command_root) as (client,headers,cid,ids,_):
        graph={'runtime_version':2,'nodes':[{'id':'develop','kind':'role','title':'开发','role_id':ids[0],'task':'[WF_BUILD]',
            'tools':['workspace_read','workspace_write'],'result_keys':['round']},approval('gate')],'edges':[['develop','gate']]}
        rid=await launch(client,headers,cid,graph)
        old=await run_state(client,headers,cid,rid,lambda r:r['status']=='waiting')
        base=f'/api/conversations/{cid}/workflows/graphs/run/{rid}'
        response=await client.post(base+'/edit',headers=headers,json={'expected_graph_revision':1,'mutation_key':'add-review',
            'operations':[{'op':'add_node','node':{'id':'review','kind':'role','title':'审查','role_id':ids[1],'task':'[WF_REVIEW]',
                'tools':['workspace_read'],'inputs':['develop'],'result_keys':['approved']}},
                {'op':'add_node','node':{'id':'join','kind':'join','title':'汇合','inputs':['review','gate']}},
                {'op':'connect','source':'develop','target':'review'}, {'op':'connect','source':'review','target':'join'},
                {'op':'connect','source':'gate','target':'join'}]})
        assert response.status_code==200,response.text
        from app.workflows.engine import advance
        await advance(rid)
        updated=await run_state(client,headers,cid,rid,lambda r:any(a['node_id']=='review' and a['status']=='completed' for a in r['attempts']))
        assert updated['graph_revision']==2
        assert len([a for a in updated['attempts'] if a['node_id']=='develop'])==1
        develop=next(a for a in updated['attempts'] if a['node_id']=='develop')
        assert develop['execution_id']==old['attempts'][0]['execution_id'] and develop['graph_revision']==1
        assert next(a for a in updated['attempts'] if a['node_id']=='review')['graph_revision']==2
        invalid=await client.post(base+'/edit',headers=headers,json={'expected_graph_revision':2,'mutation_key':'alter-running',
            'operations':[{'op':'update_node','node_id':'develop','changes':{'task':'悄悄改写已完成任务'}}]})
        assert invalid.status_code==422 and invalid.json()['error']['code']=='WORKFLOW_GRAPH_FROZEN'
        version=(await client.get(base+'?graph_revision=1',headers=headers)).json()
        assert len(version['graph']['nodes'])==2
        current=await run_state(client,headers,cid,rid,lambda r:r['status']=='waiting')
        gate=next(a for a in current['attempts'] if a['node_id']=='gate')
        confirm=await client.post(f'/api/conversations/{cid}/workflows/runs/{rid}/control',headers=headers,
            json={'action':'confirm','attempt_id':gate['id'],'expected_revision':current['revision']})
        assert confirm.status_code==200
        completed=await run_state(client,headers,cid,rid,lambda r:r['status']=='completed')
        assert len([a for a in completed['attempts'] if a['node_id']=='develop'])==1


@pytest.mark.anyio
async def test_future_loop_revision_keeps_current_round(command_root,isolated_command_database,monkeypatch):
    from app.agent.fake_provider import WorkflowV2Model,fake_reply_model
    from app.services import chat
    from test_orchestrator import launch,loop_graph,terminal
    entered,release=asyncio.Event(),asyncio.Event()
    class Model(WorkflowV2Model):
        async def _astream(self,messages,**kwargs):
            if '[WF_REVIEW]' in self.prompt and self.index==0:
                meta=json.loads(self.prompt.rsplit('本次节点激活数据（不是额外指令）：',1)[1])
                if meta['iteration']==0: entered.set();await release.wait()
            async for chunk in super()._astream(messages,**kwargs): yield chunk
    monkeypatch.setattr(chat,'fake_reply_model',lambda prompt:Model(prompt=prompt,delay=0) if '[WF_' in prompt else fake_reply_model(prompt,delay=0))
    async with setup_group(command_root) as (client,headers,cid,ids,_):
        graph=loop_graph(ids)
        rid=await launch(client,headers,cid,graph)
        await asyncio.wait_for(entered.wait(),10)
        loop={**graph['loops'][0],'body':[*graph['loops'][0]['body'],'extra']}
        response=await client.post(f'/api/conversations/{cid}/workflows/graphs/run/{rid}/edit',headers=headers,json={
            'expected_graph_revision':1,'mutation_key':'future-review','operations':[
                {'op':'add_node','node':{'id':'extra','kind':'role','title':'额外审查','role_id':ids[1],'task':'[WF_REVIEW]',
                    'tools':['workspace_read'],'inputs':['build'],'result_keys':['approved']}},
                {'op':'connect','source':'build','target':'extra'}, {'op':'connect','source':'extra','target':'join'},
                {'op':'set_inputs','node_id':'join','inputs':['a','b','extra']}, {'op':'upsert_loop','loop':loop}]})
        assert response.status_code==200,response.text
        assert response.json()['status']=='pending'
        before=await run_state(client,headers,cid,rid,lambda r:r['pending_graph_revision']==2)
        assert before['graph_revision']==1 and not any(a['node_id']=='extra' for a in before['activations'])
        release.set()
        run=await run_state(client,headers,cid,rid,terminal)
        assert run['status']=='completed',(run['status'],run['error_code'],[(a['node_id'],a['iteration'],a['status'],a['error_code']) for a in run['attempts']])
        assert run['graph_revision']==2
        extra=[a for a in run['attempts'] if a['node_id']=='extra']
        assert len(extra)==1 and extra[0]['iteration']==1 and extra[0]['graph_revision']==2
        assert all(a['graph_revision']==1 for a in run['attempts'] if a['iteration']==0 and a['loop_id'])
        assert (command_root/'workflow-round.txt').read_text()=='round-2'


@pytest.mark.anyio
@pytest.mark.parametrize('revocation',['cancel','replace','disable','remove','world_close'])
async def test_live_graph_tools_scope_protection_and_revocation(command_root,isolated_command_database,monkeypatch,revocation):
    from app.agent.fake_provider import ScriptedChatModel,ScriptedTurn
    from app.services import chat
    tools={};entered=asyncio.Event()
    class Model(ScriptedChatModel):
        def bind_tools(self,items,**kwargs): tools.update({t.name:t for t in items});return self
        async def _astream(self,messages,**kwargs):
            entered.set();await asyncio.Event().wait()
            async for chunk in super()._astream(messages,**kwargs):yield chunk
    monkeypatch.setattr(chat,'fake_reply_model',lambda prompt:Model(turns=[ScriptedTurn(text='结束')],delay=0))
    async with setup_group(command_root) as (client,headers,cid,ids,_):
        graph={'nodes':[approval('a'),approval('b'),approval('c')],'edges':[['a','b'],['b','c']]}
        did=uuid4().hex
        assert (await write(client,headers,cid,did,graph)).status_code==200
        await client.put(f'/api/conversations/{cid}/orchestrator',headers=headers,json={'role_id':ids[3],'expected_revision':0})
        request={'role_id':ids[3],'goal':'调整流程','definition_id':did,'expected_graph_revision':1,'request_key':uuid4().hex,'protected_nodes':['a']}
        started=await client.post(f'/api/conversations/{cid}/workflows/coordination',headers=headers,json=request)
        assert started.status_code==202
        grant=started.json();await asyncio.wait_for(entered.wait(),5)
        assert set(tools)=={'workflow_read_graph','workflow_write_graph','workflow_edit_graph'}
        read=tools['workflow_read_graph'];edit=tools['workflow_edit_graph'];whole=tools['workflow_write_graph']
        partial=json.loads(await read.ainvoke({'node_ids':['a']}))
        assert partial['coverage']['complete'] is False
        refused=await whole.ainvoke({'expected_graph_revision':1,'mutation_key':'partial','graph':graph})
        assert 'WORKFLOW_GRAPH_READ_REQUIRED' in refused
        full=json.loads(await read.ainvoke({}))
        assert full['coverage']['complete']
        denied=await edit.ainvoke({'expected_graph_revision':1,'mutation_key':'protected','operations':[{'op':'update_node','node_id':'a','changes':{'title':'改保护节点'}}]})
        assert 'WORKFLOW_GRAPH_PROTECTED' in denied
        result=json.loads(await edit.ainvoke({'expected_graph_revision':1,'mutation_key':'valid','operations':[{'op':'update_node','node_id':'c','changes':{'title':'合法调整'}}]}))
        assert result['graph_revision']==2
        repeat=json.loads(await edit.ainvoke({'expected_graph_revision':1,'mutation_key':'valid','operations':[{'op':'update_node','node_id':'c','changes':{'title':'合法调整'}}]}))
        assert repeat['replayed']
        cross_tool=await whole.ainvoke({'expected_graph_revision':2,'mutation_key':'valid','graph':result['graph']})
        assert 'WORKFLOW_GRAPH_MUTATION_CONFLICT' in cross_tool
        from app.workflows.graph_tools import invoke
        assert 'WORKFLOW_GRAPH_SCOPE' in await invoke(grant['execution_id'],'workflow_start',{'expected_graph_revision':2})
        if revocation=='cancel':
            listing=(await client.get(f'/api/conversations/{cid}/workflows',headers=headers)).json()
            current=next(c for c in listing['coordinations'] if c['id']==grant['id'])
            response=await client.post(f'/api/conversations/{cid}/workflows/coordination/{grant["id"]}/cancel',headers=headers,json={'expected_revision':current['revision']})
        elif revocation=='replace':
            response=await client.put(f'/api/conversations/{cid}/orchestrator',headers=headers,json={'role_id':ids[1],'expected_revision':1})
        elif revocation=='remove':
            response=await client.put(f'/api/conversations/{cid}/members',headers=headers,json={'role_ids':ids[:3],'expected_revision':1})
        elif revocation=='world_close':
            from app.db import SessionLocal
            from app.runtime.models import RuntimeGate
            async with SessionLocal() as session:
                (await session.get(RuntimeGate,1)).closing=True
                await session.commit()
            response=None
        else:
            role=next(r for r in (await client.get('/api/roles',headers=headers)).json() if r['id']==ids[3])
            response=await client.put(f'/api/roles/{ids[3]}',headers=headers,json={**role,'active':False})
        assert response is None or response.status_code==200
        late=await edit.ainvoke({'expected_graph_revision':2,'mutation_key':'late','operations':[{'op':'update_node','node_id':'c','changes':{'title':'不应提交'}}]})
        assert 'REJECTED' in late or '失效' in late or 'WORKFLOW_COORDINATION_REVOKED' in late or 'ORCHESTRATOR_APPOINTMENT_CHANGED' in late or 'WORKFLOW_WORLD_CLOSING' in late
        current=(await client.get(f'/api/conversations/{cid}/workflows/graphs/definition/{did}',headers=headers)).json()
        assert current['graph_revision']==2


@pytest.mark.anyio
async def test_result_schema_correction_and_duplicate(command_root,isolated_command_database,monkeypatch):
    from app.agent.fake_provider import ScriptedChatModel,ScriptedTurn
    from app.services import chat
    from test_orchestrator import launch,terminal
    turns=[ScriptedTurn(tool_calls=[{'name':'workflow_result','args':{'values':{'approved':value}},'id':str(index)}]) for index,value in enumerate(['true',True,True])]
    turns.append(ScriptedTurn(text='结果已提交。'))
    monkeypatch.setattr(chat,'fake_reply_model',lambda prompt:ScriptedChatModel(turns=turns,delay=0))
    async with setup_group(command_root) as (client,headers,cid,ids,_):
        graph={'runtime_version':2,'nodes':[{'id':'report','kind':'role','title':'报告','role_id':ids[3],'tools':[],'task':'报告布尔结果','result_schema':{'approved':'boolean'}},
            {'id':'choose','kind':'condition','title':'判断','condition':{'sources':['report'],'key':'approved','value':True}},
            {'id':'yes','kind':'join','title':'通过'},{'id':'no','kind':'join','title':'不通过'}],
            'edges':[['report','choose'],['choose','yes'],['choose','no']],
            'edge_rules':[{'source':'choose','target':'yes','when':'true'},{'source':'choose','target':'no','when':'false'}]}
        rid=await launch(client,headers,cid,graph)
        run=await run_state(client,headers,cid,rid,terminal)
        assert run['status']=='completed'
        report=next(a for a in run['attempts'] if a['node_id']=='report')
        assert report['result']['values']['approved'] is True
        assert next(a for a in run['attempts'] if a['node_id']=='yes')['status']=='completed'
        assert next(a for a in run['attempts'] if a['node_id']=='no')['status']=='skipped'
        assert run['used_decisions']==4


@pytest.mark.anyio
async def test_plain_mention_has_no_management_grant(command_root,isolated_command_database,monkeypatch):
    from app.agent.fake_provider import ScriptedChatModel,ScriptedTurn
    from app.services import chat
    from app.db import SessionLocal
    from app.models import AgentExecution
    from sqlalchemy import select
    from app.workflows.graph_tools import invoke
    entered=asyncio.Event()
    class Model(ScriptedChatModel):
        async def _astream(self,messages,**kwargs):
            entered.set();await asyncio.Event().wait()
            async for chunk in super()._astream(messages,**kwargs):yield chunk
    monkeypatch.setattr(chat,'fake_reply_model',lambda prompt:Model(turns=[ScriptedTurn(text='普通回复')],delay=0))
    async with setup_group(command_root) as (client,headers,cid,ids,_):
        await client.put(f'/api/conversations/{cid}/orchestrator',headers=headers,json={'role_id':ids[3],'expected_revision':0})
        response=await client.post(f'/api/conversations/{cid}/messages',headers=headers,json={'parts':[{'type':'text','text':'普通讨论工作流'}],'mentions':[ids[3]]})
        assert response.status_code==202
        await asyncio.wait_for(entered.wait(),5)
        async with SessionLocal() as session: execution=await session.scalar(select(AgentExecution).where(AgentExecution.conversation_id==cid))
        result=await invoke(execution.execution_id,'workflow_read_graph',{})
        assert 'WORKFLOW_COORDINATION_REVOKED' in result
        listing=(await client.get(f'/api/conversations/{cid}/workflows',headers=headers)).json()
        assert listing['coordinations']==[] and listing['definitions']==[]


@pytest.mark.anyio
async def test_design_continuation_and_restart_do_not_reset_budget(command_root,isolated_command_database,monkeypatch):
    from app.agent.fake_provider import ScriptedChatModel,ScriptedTurn
    from app.services import chat
    from app.db import SessionLocal
    from app.models import CoordinationSession,WorkflowBudget
    from app.workflows.planning import recover
    monkeypatch.setattr(chat,'fake_reply_model',lambda prompt:ScriptedChatModel(turns=[ScriptedTurn(text='本次只讨论，未声称提交图。')],delay=0))
    async with setup_group(command_root) as (client,headers,cid,ids,_):
        did=uuid4().hex
        await write(client,headers,cid,did,{'nodes':[approval()],'edges':[]})
        await client.put(f'/api/conversations/{cid}/orchestrator',headers=headers,json={'role_id':ids[3],'expected_revision':0})
        body={'role_id':ids[3],'goal':'讨论草稿','definition_id':did,'expected_graph_revision':1,'request_key':uuid4().hex}
        first=(await client.post(f'/api/conversations/{cid}/workflows/coordination',headers=headers,json=body)).json()
        for _ in range(200):
            data=(await client.get(f'/api/conversations/{cid}/workflows',headers=headers)).json()
            if data['coordinations'][0]['status']=='completed': break
            await asyncio.sleep(.025)
        assert data['coordinations'][0]['used_decisions']==1
        # 模拟模型已结束、协调会话收口前重启，不重新派发原执行。
        from app.workflows import service
        async with service.control_lock,SessionLocal() as session:
            previous=await session.get(CoordinationSession,first['id'])
            previous.status='running'
            await recover(session);await session.commit()
        body.update(goal='继续调整同一草稿',request_key=uuid4().hex,continue_session_id=first['id'])
        second=await client.post(f'/api/conversations/{cid}/workflows/coordination',headers=headers,json=body)
        assert second.status_code==202,second.text
        assert second.json()['chain_id']==first['chain_id']
        for _ in range(200):
            data=(await client.get(f'/api/conversations/{cid}/workflows',headers=headers)).json()
            current=next(g for g in data['coordinations'] if g['id']==second.json()['id'])
            if current['status']=='completed': break
            await asyncio.sleep(.025)
        assert current['used_decisions']==2
        async with SessionLocal() as session:
            from sqlalchemy import select,func
            assert await session.scalar(select(func.count()).select_from(WorkflowBudget).where(WorkflowBudget.conversation_id==cid))==1
        assert not data['runs'] and len(data['definitions'])==1


@pytest.mark.anyio
async def test_stop_races_graph_commit_without_reactivation(command_root,isolated_command_database):
    from test_orchestrator import launch,terminal
    async with setup_group(command_root) as (client,headers,cid,_,_):
        rid=await launch(client,headers,cid,{'runtime_version':2,'nodes':[approval()],'edges':[]})
        run=await run_state(client,headers,cid,rid,lambda r:r['status']=='waiting')
        stop,edit=await asyncio.gather(
            client.post(f'/api/conversations/{cid}/workflows/runs/{rid}/control',headers=headers,json={'action':'stop','expected_revision':run['revision']}),
            client.post(f'/api/conversations/{cid}/workflows/graphs/run/{rid}/edit',headers=headers,json={'expected_graph_revision':1,'mutation_key':'race',
                'operations':[{'op':'add_node','node':approval('next')},{'op':'connect','source':'first','target':'next'}]}))
        assert (stop.status_code,edit.status_code) in [(200,409),(409,200)]
        if stop.status_code==409:
            current=await run_state(client,headers,cid,rid,lambda r:r['status']=='waiting')
            stop=await client.post(f'/api/conversations/{cid}/workflows/runs/{rid}/control',headers=headers,json={'action':'stop','expected_revision':current['revision']})
            assert stop.status_code==200
        stopped=await run_state(client,headers,cid,rid,terminal)
        assert stopped['status']=='stopped'
        late=await client.post(f'/api/conversations/{cid}/workflows/graphs/run/{rid}/edit',headers=headers,json={'expected_graph_revision':stopped['latest_graph_revision'],'mutation_key':'late',
            'operations':[{'op':'set_concurrency','concurrency':1}]})
        assert late.status_code==409
        assert all(a['execution_id'] is None for a in stopped['attempts'])


@pytest.mark.anyio
async def test_two_loop_revisions_wait_for_both_boundaries(command_root,isolated_command_database):
    from test_orchestrator import launch,terminal
    async with setup_group(command_root) as (client,headers,cid,_,_):
        nodes=[];edges=[];loops=[]
        for prefix in ['a','b']:
            nodes.extend([approval(prefix),{'id':prefix+'c','kind':'condition','title':prefix+'判断','condition':{'sources':[prefix],'key':'approved','value':True}},
                {'id':prefix+'e','kind':'join','title':prefix+'结束'}])
            edges.extend([[prefix,prefix+'c'],[prefix+'c',prefix],[prefix+'c',prefix+'e']])
            loops.append({'id':prefix,'entry':prefix,'decision':prefix+'c','exit':prefix+'e','body':[prefix,prefix+'c'],'max_iterations':3})
        rid=await launch(client,headers,cid,{'runtime_version':2,'nodes':nodes,'edges':edges,'loops':loops,'entries':['a','b']})
        await run_state(client,headers,cid,rid,lambda r:r['status']=='waiting')
        ops=[]
        for loop in loops:
            prefix=loop['id'];extra=prefix+'2'
            ops.extend([{'op':'add_node','node':approval(extra)},{'op':'disconnect','source':prefix,'target':prefix+'c'},
                {'op':'connect','source':prefix,'target':extra},{'op':'connect','source':extra,'target':prefix+'c'},
                {'op':'upsert_loop','loop':{**loop,'body':[prefix,extra,prefix+'c']}}])
        changed=await client.post(f'/api/conversations/{cid}/workflows/graphs/run/{rid}/edit',headers=headers,json={'expected_graph_revision':1,'mutation_key':'both-loops','operations':ops})
        assert changed.status_code==200,changed.text
        async def confirm(nid,iteration,decision):
            for _ in range(5):
                run=await run_state(client,headers,cid,rid,lambda r:any(a['node_id']==nid and a['iteration']==iteration and a['status']=='waiting' for a in r['attempts']))
                attempt=next(a for a in run['attempts'] if a['node_id']==nid and a['iteration']==iteration and a['status']=='waiting')
                response=await client.post(f'/api/conversations/{cid}/workflows/runs/{rid}/control',headers=headers,json={'action':'confirm','attempt_id':attempt['id'],'decision':decision,'expected_revision':run['revision']})
                if response.status_code==200:return
                assert response.status_code==409
            raise AssertionError('人工确认版本未稳定')
        await confirm('a',0,False)
        parked=await run_state(client,headers,cid,rid,lambda r:any(a['node_id']=='ac' and a['status']=='completed' for a in r['attempts']))
        assert parked['graph_revision']==1 and parked['pending_graph_revision']==2
        assert not any(a['node_id']=='a2' for a in parked['activations'])
        await confirm('b',0,False)
        ready=await run_state(client,headers,cid,rid,lambda r:r['graph_revision']==2)
        assert ready['loop_states']['a']['iteration']==ready['loop_states']['b']['iteration']==1
        for nid in ['a','b','a2','b2']: await confirm(nid,1,True)
        finished=await run_state(client,headers,cid,rid,terminal)
        assert finished['status']=='completed' and finished['used_decisions']==0
        assert all(a['graph_revision']==2 and a['iteration']==1 for a in finished['attempts'] if a['node_id'] in ('a2','b2'))


@pytest.mark.anyio
async def test_readded_node_and_cross_version_retry_keep_activation_history(command_root,isolated_command_database):
    from test_orchestrator import launch,terminal
    async with setup_group(command_root) as (client,headers,cid,_,_):
        rid=await launch(client,headers,cid,{'runtime_version':2,'nodes':[approval('gate'),approval('child')],'edges':[['gate','child']]})
        run=await run_state(client,headers,cid,rid,lambda r:r['status']=='waiting')
        old_gate=next(a for a in run['attempts'] if a['node_id']=='gate')
        base=f'/api/conversations/{cid}/workflows/graphs/run/{rid}/edit'
        removed=await client.post(base,headers=headers,json={'expected_graph_revision':1,'mutation_key':'remove-child','operations':[
            {'op':'disconnect','source':'gate','target':'child'},{'op':'remove_node','node_id':'child'}]})
        assert removed.status_code==200
        added=await client.post(base,headers=headers,json={'expected_graph_revision':2,'mutation_key':'readd-child','operations':[
            {'op':'add_node','node':approval('child')},{'op':'connect','source':'gate','target':'child'}]})
        assert added.status_code==200,added.text
        run=await run_state(client,headers,cid,rid,lambda r:r['graph_revision']==3)
        children=[a for a in run['activations'] if a['node_id']=='child']
        assert len(children)==2 and len({a['id'] for a in children})==2
        assert {a['graph_revision'] for a in children}=={1,3}
        async def confirm(nid):
            current=await run_state(client,headers,cid,rid,lambda r:r['status']=='waiting' and any(a['node_id']==nid and a['current'] and a['status']=='waiting' for a in r['attempts']))
            a=next(a for a in current['attempts'] if a['node_id']==nid and a['current'])
            result=await client.post(f'/api/conversations/{cid}/workflows/runs/{rid}/control',headers=headers,json={'action':'confirm','expected_revision':current['revision'],'attempt_id':a['id']})
            assert result.status_code==200
        await confirm('gate');await confirm('child')
        run=await run_state(client,headers,cid,rid,terminal)
        assert run['status']=='completed'
        retry=await client.post(f'/api/conversations/{cid}/workflows/runs/{rid}/control',headers=headers,json={'action':'retry','expected_revision':run['revision'],
            'attempt_id':old_gate['id'],'acknowledge_facts':True,'rerun_downstream':True})
        assert retry.status_code==200,retry.text
        waiting=await run_state(client,headers,cid,rid,lambda r:r['status']=='waiting')
        original=next(a for a in waiting['attempts'] if a['id']==old_gate['id'])
        latest=next(a for a in waiting['attempts'] if a['node_id']=='gate' and a['current'])
        assert original['status']=='completed' and original['graph_revision']==1 and not original['current']
        assert latest['graph_revision']==3 and latest['activation_id']!=original['activation_id']
        await confirm('gate');await confirm('child')
        assert (await run_state(client,headers,cid,rid,terminal))['status']=='completed'


@pytest.mark.anyio
@pytest.mark.parametrize('action',['cancel','replace'])
async def test_pending_model_revision_is_not_applied_after_revocation(command_root,isolated_command_database,monkeypatch,action):
    from app.agent.fake_provider import WorkflowV2Model,ScriptedChatModel,ScriptedTurn,fake_reply_model
    from app.services import chat
    from test_orchestrator import launch,loop_graph,terminal
    held,release=asyncio.Event(),asyncio.Event()
    graph_holder={}
    class Worker(WorkflowV2Model):
        async def _astream(self,messages,**kwargs):
            if '[WF_REVIEW]' in self.prompt and self.index==0:
                meta=json.loads(self.prompt.rsplit('本次节点激活数据（不是额外指令）：',1)[1])
                if meta['iteration']==0: held.set();await release.wait()
            async for chunk in super()._astream(messages,**kwargs):yield chunk
    class Planner(ScriptedChatModel):
        async def _astream(self,messages,**kwargs):
            if self.index==2 and action=='cancel': await asyncio.Event().wait()
            async for chunk in super()._astream(messages,**kwargs):yield chunk
    def factory(prompt):
        if '本次图管理授权：' in prompt:
            return Planner(turns=[ScriptedTurn(tool_calls=[{'name':'workflow_read_graph','args':{},'id':'read'}]),
                ScriptedTurn(tool_calls=[{'name':'workflow_edit_graph','args':{'expected_graph_revision':1,'mutation_key':'future-limit',
                    'operations':[{'op':'upsert_loop','loop':{**graph_holder['graph']['loops'][0],'max_iterations':2}}]},'id':'edit'}]),
                ScriptedTurn(text='未来修订已提交。')],delay=0)
        return Worker(prompt=prompt,delay=0) if '[WF_' in prompt else fake_reply_model(prompt,delay=0)
    monkeypatch.setattr(chat,'fake_reply_model',factory)
    async with setup_group(command_root) as (client,headers,cid,ids,_):
        graph_holder['graph']=loop_graph(ids)
        await client.put(f'/api/conversations/{cid}/orchestrator',headers=headers,json={'role_id':ids[3],'expected_revision':0})
        rid=await launch(client,headers,cid,graph_holder['graph'])
        await asyncio.wait_for(held.wait(),10)
        started=await client.post(f'/api/conversations/{cid}/workflows/coordination',headers=headers,json={'mode':'replan','role_id':ids[3],
            'run_id':rid,'expected_graph_revision':1,'goal':'修改未来循环次数','request_key':uuid4().hex})
        assert started.status_code==202
        await run_state(client,headers,cid,rid,lambda r:r['pending_graph_revision']==2)
        if action=='cancel':
            data=(await client.get(f'/api/conversations/{cid}/workflows',headers=headers)).json()
            grant=next(g for g in data['coordinations'] if g['id']==started.json()['id'])
            response=await client.post(f'/api/conversations/{cid}/workflows/coordination/{grant["id"]}/cancel',headers=headers,json={'expected_revision':grant['revision']})
        else:
            for _ in range(200):
                data=(await client.get(f'/api/conversations/{cid}/workflows',headers=headers)).json()
                grant=next(g for g in data['coordinations'] if g['id']==started.json()['id'])
                if grant['status']=='completed':break
                await asyncio.sleep(.025)
            assert grant['status']=='completed'
            response=await client.put(f'/api/conversations/{cid}/orchestrator',headers=headers,json={'role_id':ids[1],'expected_revision':1})
        assert response.status_code==200
        release.set()
        run=await run_state(client,headers,cid,rid,terminal)
        assert run['status']=='completed' and run['graph_revision']==1
        assert run['graph_versions'][-1]['status']=='not_applied'
        assert all(a['graph_revision']==1 for a in run['attempts'])


@pytest.mark.anyio
async def test_delegated_retry_requires_observed_known_write_facts(command_root,isolated_command_database,monkeypatch):
    from app.agent.fake_provider import ScriptedChatModel,ScriptedTurn,fake_reply_model
    from app.services import chat
    from app.db import SessionLocal
    from app.models import FileEffect
    from sqlalchemy import select
    from test_orchestrator import launch
    captured={};entered=asyncio.Event()
    class Planner(ScriptedChatModel):
        def bind_tools(self,tools,**kwargs): captured.update({t.name:t for t in tools});return self
        async def _astream(self,messages,**kwargs):
            entered.set();await asyncio.Event().wait()
            async for chunk in super()._astream(messages,**kwargs):yield chunk
    monkeypatch.setattr(chat,'fake_reply_model',lambda prompt:Planner(turns=[ScriptedTurn(text='核对')],delay=0) if '本次图管理授权：' in prompt else fake_reply_model(prompt,delay=0))
    async with setup_group(command_root) as (client,headers,cid,ids,_):
        await client.put(f'/api/conversations/{cid}/orchestrator',headers=headers,json={'role_id':ids[3],'expected_revision':0})
        rid=await launch(client,headers,cid,{'runtime_version':2,'nodes':[{'id':'work','kind':'role','title':'开发','role_id':ids[0],
            'task':'[WF_BUILD]','tools':['workspace_read','workspace_write'],'result_keys':['round']},approval('gate')],'edges':[['work','gate']]})
        run=await run_state(client,headers,cid,rid,lambda r:r['status']=='waiting')
        source=next(a for a in run['attempts'] if a['node_id']=='work')
        async with SessionLocal() as session:
            effects=(await session.scalars(select(FileEffect).where(FileEffect.execution_id==source['execution_id']))).all()
            assert effects
            for effect in effects: effect.payload_encrypted=None
            await session.commit()
        response=await client.post(f'/api/conversations/{cid}/workflows/coordination',headers=headers,json={'role_id':ids[3],'mode':'replan','run_id':rid,
            'expected_graph_revision':1,'goal':'核对失败后是否可重试','request_key':uuid4().hex})
        assert response.status_code==202
        await asyncio.wait_for(entered.wait(),5)
        payload={'action':'retry','attempt_id':source['id'],'expected_revision':run['revision'],'acknowledge_facts':True}
        unobserved=await captured['workflow_control'].ainvoke(payload)
        assert 'WORKFLOW_RETRY_REVIEW_REQUIRED' in unobserved
        inspected=json.loads(await captured['workflow_inspect_run'].ainvoke({}))
        assert inspected['file_evidence'][0]['state']=='unknown'
        refused=await captured['workflow_control'].ainvoke({**payload,'expected_revision':inspected['revision']})
        assert 'WORKFLOW_RETRY_REVIEW_REQUIRED' in refused
        after=await run_state(client,headers,cid,rid,lambda r:True)
        assert len(after['attempts'])==len(run['attempts'])
        assert (command_root/'workflow-round.txt').read_text()=='round-1'


@pytest.mark.anyio
async def test_incomplete_condition_and_loop_are_plannable_drafts(command_root,isolated_command_database,monkeypatch):
    from app.agent.fake_provider import ScriptedChatModel,ScriptedTurn
    from app.services import chat
    monkeypatch.setattr(chat,'fake_reply_model',lambda prompt:ScriptedChatModel(turns=[
        ScriptedTurn(tool_calls=[{'name':'workflow_read_graph','args':{},'id':'read'}]),
        ScriptedTurn(tool_calls=[{'name':'workflow_edit_graph','args':{'expected_graph_revision':1,'mutation_key':'complete-draft','operations':[
            {'op':'update_node','node_id':'check','changes':{'kind':'approval'}},{'op':'set_condition','node_id':'check','condition':None},
            {'op':'remove_loop','loop_id':'draft'}]},'id':'complete'}]),ScriptedTurn(text='草稿已补齐。')],delay=0))
    async with setup_group(command_root) as (client,headers,cid,ids,_):
        did=uuid4().hex
        saved=await write(client,headers,cid,did,{'nodes':[{'id':'check','kind':'condition','title':'尚未配置判断','condition':{'sources':[]}}],
            'edges':[],'loops':[{'id':'draft','body':[]}]})
        assert saved.status_code==200 and not saved.json()['executable']
        await client.put(f'/api/conversations/{cid}/orchestrator',headers=headers,json={'role_id':ids[3],'expected_revision':0})
        request=await client.post(f'/api/conversations/{cid}/workflows/coordination',headers=headers,json={'role_id':ids[3],'definition_id':did,
            'expected_graph_revision':1,'goal':'把未配置判断改成人工确认，去掉尚未配置的循环','request_key':uuid4().hex})
        assert request.status_code==202
        for _ in range(200):
            listing=(await client.get(f'/api/conversations/{cid}/workflows',headers=headers)).json()
            if listing['coordinations'][0]['status']=='completed':break
            await asyncio.sleep(.025)
        current=(await client.get(f'/api/conversations/{cid}/workflows/graphs/definition/{did}',headers=headers)).json()
        assert current['graph_revision']==2 and current['compile_issues']==[]
        assert not listing['runs']


@pytest.mark.anyio
async def test_graph_routes_reject_guest_and_cross_conversation_target(command_root,isolated_command_database):
    from accounts import guest_username,TEST_PASSWORD
    async with setup_group(command_root) as (client,headers,cid,ids,_):
        did=uuid4().hex
        assert (await write(client,headers,cid,did,{'nodes':[approval()],'edges':[]})).status_code==200
        guest=(await client.post('/api/auth/register',json={'username':guest_username('graph'),'nickname':'Guest','password':TEST_PASSWORD})).json()
        gh={'Authorization':f"Bearer {guest['access_token']}"}
        base=f'/api/conversations/{cid}/workflows'
        assert (await client.get(base+f'/graphs/definition/{did}',headers=gh)).status_code==403
        assert (await write(client,gh,cid,did,{'nodes':[approval()],'edges':[]},version=1)).status_code==403
        assert (await client.post(base+'/coordination',headers=gh,json={'role_id':ids[3],'goal':'规划','request_key':uuid4().hex})).status_code==403
        other=(await client.post('/api/conversations',headers=headers,json={'type':'group','title':'另一群','role_ids':ids[:2]})).json()['id']
        assert (await client.get(f'/api/conversations/{other}/workflows/graphs/definition/{did}',headers=headers)).status_code==404
        assert (await write(client,headers,other,did,{'nodes':[approval()],'edges':[]},version=1)).status_code==404


@pytest.mark.anyio
async def test_result_updates_use_explicit_revision_and_freeze_after_handoff(command_root,isolated_command_database,monkeypatch):
    from app.agent.fake_provider import ScriptedChatModel,ScriptedTurn
    from app.services import chat
    from test_orchestrator import launch,terminal
    captured={};entered=asyncio.Event();release=asyncio.Event()
    class Model(ScriptedChatModel):
        def bind_tools(self,tools,**kwargs): captured.update({t.name:t for t in tools});return self
        async def _astream(self,messages,**kwargs):
            entered.set();await release.wait()
            async for chunk in super()._astream(messages,**kwargs):yield chunk
    monkeypatch.setattr(chat,'fake_reply_model',lambda prompt:Model(turns=[ScriptedTurn(text='完成')],delay=0))
    async with setup_group(command_root) as (client,headers,cid,ids,_):
        rid=await launch(client,headers,cid,{'runtime_version':2,'nodes':[{'id':'report','kind':'role','title':'报告','role_id':ids[3],
            'task':'报告明确布尔值','tools':[],'result_schema':{'approved':'boolean'}}],'edges':[]})
        await asyncio.wait_for(entered.wait(),5)
        tool=captured['workflow_result']
        responses=await asyncio.gather(*[tool.ainvoke({'values':{'approved':value},'expected_result_revision':0}) for value in [True,False]])
        assert sum('"recorded": true' in result for result in responses)==1
        assert sum('WORKFLOW_RESULT_REVISION_CONFLICT' in result for result in responses)==1
        current=await run_state(client,headers,cid,rid,lambda r:any(a['result'] for a in r['attempts']))
        value=current['attempts'][0]['result']['values']['approved']
        updated=json.loads(await tool.ainvoke({'values':{'approved':not value},'expected_result_revision':1}))
        assert updated['result_revision']==2
        release.set()
        final=await run_state(client,headers,cid,rid,terminal)
        assert final['status']=='completed' and final['attempts'][0]['result']['revision']==2
        late=await tool.ainvoke({'values':{'approved':value},'expected_result_revision':2})
        assert 'WORKFLOW_ALLOCATION_REVOKED' in late
