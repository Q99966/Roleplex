"""统一图读取、整图写入及 ID 编辑；与派发共用短事务控制边界。"""
import copy
import hashlib
import json
import logging
from uuid import uuid4
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from ..db import SessionLocal
from ..models import WorkflowDefinition, WorkflowRun, WorkflowGraphRevision
from ..realtime import store as events
from . import service
from .graph_schemas import DraftGraph, WriteGraph, EditGraph
from .schemas import Graph, serial_execution_graph
from .graph import compile_graph

GRAPH_FIELDS = ('nodes','edges','entries','edge_rules','loops','concurrency','runtime_version','presentation')
DISPLAY_FIELDS = {'position','color'}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def problem(code, *, status=422, operation=None, node_id=None, fields=None):
    """只输出可定位的安全字段，不包含失败输入、Prompt 或异常正文。"""
    detail={'code':code}
    if operation is not None: detail['operation_index']=operation
    if node_id is not None: detail['node_id']=node_id
    if fields is not None: detail['fields']=fields
    raise HTTPException(status, {'code':code,'details':{k:v for k,v in detail.items() if k!='code'}})


def normalize(raw, operations=None, *, default_version=2):
    raw={**raw,'runtime_version':raw.get('runtime_version',default_version)}
    try: return DraftGraph.model_validate(raw).model_dump(mode='json')
    except ValidationError as exc:
        locations=[list(e['loc']) for e in exc.errors(include_input=False,include_context=False)]
        candidates=set()
        for loc in locations:
            value=raw
            try:
                if loc[0]=='nodes': candidates.add(raw['nodes'][loc[1]]['id'])
                for part in loc: value=value[part]
                if isinstance(value,str): candidates.add(value)
            except (KeyError,IndexError,TypeError): pass
        operation=None
        if operations:
            for index,item in reversed(list(enumerate(operations))):
                data=item.model_dump(mode='json')
                references={data[k] for k in ('node_id','source','target','loop_id') if isinstance(data.get(k),str)}
                if isinstance(data.get('node'),dict): references.add(data['node']['id'])
                if candidates & references: operation=index;break
        problem('WORKFLOW_GRAPH_INVALID',operation=operation,fields=locations)


def compile_checked(graph):
    try:
        complete=Graph.model_validate(graph).model_dump(mode='json')
        from .results import contract
        for node in complete['nodes']: contract(complete,node)
        return compile_graph(complete) if complete['runtime_version']==2 else serial_execution_graph(complete)
    except ValidationError as exc:
        problem('WORKFLOW_GRAPH_NOT_EXECUTABLE', fields=[list(e['loc']) for e in exc.errors(include_input=False, include_context=False)])


def issues(graph):
    try: compile_checked(graph); return []
    except HTTPException as exc:
        return [exc.detail if isinstance(exc.detail,dict) else {'code':str(exc.detail)}]


def structure(node):
    return {k:v for k,v in node.items() if k not in DISPLAY_FIELDS}


def trim_presentation(graph):
    """只清理展示引用；删除任务/连线时不要求额外修复无执行语义的标签。"""
    presentation = graph.get('presentation')
    if not presentation: return
    ids = {node['id'] for node in graph['nodes']}
    groups = [{**group, 'node_ids': [nid for nid in group['node_ids'] if nid in ids]} for group in presentation.get('groups', [])]
    graph['presentation'] = {**presentation, 'groups': [group for group in groups if group['node_ids']],
        'edge_labels': [label for label in presentation.get('edge_labels', []) if [label['source'], label['target']] in [list(edge) for edge in graph['edges']]]}


def diff(before, after):
    old={n['id']:n for n in before['nodes']};new={n['id']:n for n in after['nodes']}
    return {'added':sorted(new.keys()-old.keys()), 'removed':sorted(old.keys()-new.keys()),
        'updated':sorted(n for n in old.keys()&new.keys() if old[n]!=new[n]),
        'edges_added':[e for e in after['edges'] if e not in before['edges']],
        'edges_removed':[e for e in before['edges'] if e not in after['edges']],
        'configuration_changed':[k for k in GRAPH_FIELDS if k not in ('nodes','edges') and before.get(k)!=after.get(k)]}


async def target(session, cid, uid, kind, target_id):
    await service.owned(session,cid,uid)
    if kind=='definition':
        row=await session.get(WorkflowDefinition,target_id)
        if row and row.conversation_id!=cid: service.reject('WORKFLOW_NOT_FOUND',404)
        return row, row.revision if row else 0, normalize(row.graph,default_version=1) if row else DraftGraph().model_dump(mode='json')
    row=await service.get_run(session,cid,uid,target_id)
    if row.runtime_version!=2: service.reject('WORKFLOW_VERSION_REQUIRED',422)
    latest=await session.scalar(select(WorkflowGraphRevision).where(WorkflowGraphRevision.target_key=='run:'+target_id).order_by(WorkflowGraphRevision.number.desc()))
    return row, latest.number if latest else 0, normalize(latest.graph if latest else {k:row.snapshot[k] for k in GRAPH_FIELDS if k in row.snapshot})


async def member_capabilities(session, conv, uid):
    from .coordination import capabilities
    from ..agent.capabilities import role_tool_policy
    from ..models import Role
    result=await capabilities(session,conv,uid)
    for item in result:
        role=await session.get(Role,item['role_id'])
        policy=await role_tool_policy(session,conversation=conv,role=role,triggered_by_user_id=uid)
        item['tool_specs']=policy['exposed_tools']
    return result


async def validate_authority(session, cid, uid, graph):
    """草稿可不选角色；已指定成员和显式工具仍必须在本群授权内。"""
    from .coordination import capabilities
    from ..config import settings
    conv=await service.owned(session,cid,uid)
    caps={r['role_id']:set(r['tools']) for r in await capabilities(session,conv,uid)}
    if graph.get('concurrency') and graph['concurrency']>settings.workflow_parallelism:
        problem('WORKFLOW_CONCURRENCY_UNAVAILABLE')
    from .results import contract
    for node in graph['nodes']:
        contract(graph,node)
        rid=node.get('role_id')
        if rid is not None and rid not in caps: problem('WORKFLOW_ROLE_UNAVAILABLE',node_id=node['id'])
        requested=node.get('tools')
        available=caps.get(rid,set().union(*caps.values()) if caps else set()) if node['kind']=='role' else set()
        if requested is not None and (len(requested)!=len(set(requested)) or not set(requested).issubset(available)):
            problem('WORKFLOW_TOOL_NOT_GRANTED',node_id=node['id'])


def constraints_for(graph, protected):
    ids={n['id'] for n in graph['nodes']}
    if not set(protected).issubset(ids): problem('WORKFLOW_GRAPH_INVALID')
    return {'nodes':{n['id']:structure(n) for n in graph['nodes'] if n['id'] in protected},
        'edges':[e for e in graph['edges'] if any(x in protected for x in e)],
        'loops':[l for l in graph.get('loops',[]) if set(l['body'])&set(protected)]}


def validate_constraints(graph, constraints):
    if not constraints: return
    nodes={n['id']:structure(n) for n in graph['nodes']}
    for nid,node in constraints.get('nodes',{}).items():
        if nodes.get(nid)!=node: problem('WORKFLOW_GRAPH_PROTECTED',node_id=nid)
        edges=[e for e in graph['edges'] if nid in e]
        if sorted(edges)!=sorted(e for e in constraints.get('edges',[]) if nid in e): problem('WORKFLOW_GRAPH_PROTECTED',node_id=nid)
    loops={l['id']:l for l in graph.get('loops',[])}
    if any(loops.get(l['id'])!=l for l in constraints.get('loops',[])): problem('WORKFLOW_GRAPH_PROTECTED')


def apply_operations(before, operations):
    graph=copy.deepcopy(before)
    for index, operation in enumerate(operations):
        op=operation.model_dump(mode='json',exclude_unset=True)
        action=op['op'];nid=op.get('node_id');node=next((n for n in graph['nodes'] if n['id']==nid),None)
        if action in ('update_node','remove_node','set_inputs','set_condition') and node is None:
            problem('WORKFLOW_NODE_NOT_FOUND',operation=index,node_id=nid)
        if action=='add_node':
            if any(n['id']==op['node']['id'] for n in graph['nodes']): problem('WORKFLOW_NODE_EXISTS',operation=index,node_id=op['node']['id'])
            graph['nodes'].append(op['node'])
        elif action=='update_node': node.update(op['changes'])
        elif action=='remove_node': graph['nodes'].remove(node)
        elif action=='set_inputs': node['inputs']=op['inputs']
        elif action=='set_condition': node['condition']=op['condition']
        elif action in ('connect','disconnect'):
            edge=[op['source'],op['target']]
            graph['edge_rules']=[r for r in graph['edge_rules'] if [r['source'],r['target']]!=edge]
            if action=='disconnect': graph['edges']=[e for e in graph['edges'] if list(e)!=edge]
            else:
                if edge not in graph['edges']: graph['edges'].append(edge)
                graph['edge_rules'].append({'source':op['source'],'target':op['target'],'when':op.get('when','always')})
        elif action=='upsert_loop': graph['loops']=[l for l in graph['loops'] if l['id']!=op['loop']['id']]+[op['loop']]
        elif action=='remove_loop': graph['loops']=[l for l in graph['loops'] if l['id']!=op['loop_id']]
        elif action=='set_entries': graph['entries']=op['entries']
        elif action=='set_concurrency': graph['concurrency']=op['concurrency']
        elif action=='set_presentation': graph['presentation']=op['presentation']
        if action in ('remove_node', 'disconnect'): trim_presentation(graph)
    return graph


async def record(session, *, cid, uid, kind, target_id, number, graph, mutation_key=None, request_digest=None,
                 execution_id=None, changes=None, legacy=False, status='applied'):
    row=WorkflowGraphRevision(id=uuid4().hex,conversation_id=cid,actor_id=uid,target_key=kind+':'+target_id,
        definition_id=target_id if kind=='definition' else None,run_id=target_id if kind=='run' else None,
        number=number,graph=copy.deepcopy(graph),mutation_key=mutation_key,request_digest=request_digest,
        source_execution_id=execution_id,changes_json=changes or {},legacy=legacy,status=status,created_at=service.now())
    session.add(row);await session.flush();return row


def revision_json(row):
    return {'id':row.id,'graph_revision':row.number,'graph':row.graph,'changes':row.changes_json,
        'source_execution_id':row.source_execution_id,'legacy':row.legacy,'status':row.status,'created_at':row.created_at}


async def read(cid,uid,kind,target_id,*,execution_id=None,node_ids=None,graph_revision=None):
    async with service.control_lock,SessionLocal() as session:
        if execution_id:
            from .planning import authorized
            grant=await authorized(session,execution_id,'workflow_read_graph')
            if (grant.run_id or grant.definition_id)!=target_id or ('run' if grant.run_id else 'definition')!=kind: service.reject('WORKFLOW_GRAPH_SCOPE',403)
        row,number,graph=await target(session,cid,uid,kind,target_id)
        versions=list((await session.scalars(select(WorkflowGraphRevision).where(WorkflowGraphRevision.target_key==kind+':'+target_id).order_by(WorkflowGraphRevision.number.desc()))).all())
        legacy=bool(row and not versions)
        if graph_revision is not None and graph_revision!=number:
            version=next((v for v in versions if v.number==graph_revision),None)
            if version is None: service.reject('WORKFLOW_GRAPH_REVISION_NOT_FOUND',404)
            graph,number,legacy=copy.deepcopy(version.graph),version.number,version.legacy
        all_ids={n['id'] for n in graph['nodes']};selected=all_ids
        if node_ids is not None:
            if not set(node_ids).issubset(all_ids): problem('WORKFLOW_NODE_NOT_FOUND')
            selected=set(node_ids)|{x for edge in graph['edges'] if set(edge)&set(node_ids) for x in edge}
        complete=selected==all_ids
        result_graph=copy.deepcopy(graph)
        if not complete:
            result_graph['nodes']=[n for n in graph['nodes'] if n['id'] in selected]
            result_graph['edges']=[e for e in graph['edges'] if set(e).issubset(selected)]
            trim_presentation(result_graph)
        if execution_id:
            from ..models import ExecutionAllocation
            allocation=await session.get(ExecutionAllocation,execution_id)
            allocation.authority_json={**allocation.authority_json,'complete_graph_revision':number if complete else None}
            await session.commit()
        conv=await service.owned(session,cid,uid)
        result={'target':{'kind':kind,'id':target_id},'name':row.name if kind=='definition' and row else row.snapshot['name'] if row else '协调流程',
            'graph_revision':number,'graph':result_graph,'coverage':{'complete':complete,'omitted_node_ids':sorted(all_ids-selected),
            'continue_with':'workflow_read_graph 不传 node_ids 可读取完整图'},'compile_issues':issues(graph),
            'members':await member_capabilities(session,conv,uid),'legacy':legacy,
            'versions':[{k:v for k,v in revision_json(r).items() if k!='graph'} for r in versions],
            'constraints':grant.constraints_json if execution_id else {},
            'capabilities':['read','write','edit','validate'],'execution_rules':{'loops':'disjoint_non_nested','ordinary_mentions_manage_graph':False}}
        if kind=='run':
            from .replanning import edit_scope
            result['edit_scope']=await edit_scope(session,row)
            result['task_assignments']=row.state_json.get('assignments',{})
        return result


async def mutate(cid,uid,kind,target_id,payload: WriteGraph|EditGraph,*,execution_id=None,compatibility=False):
    """写入与编辑同一事务边界；校验失败没有图、版本或幂等记录的半提交。"""
    tool='workflow_write_graph' if isinstance(payload,WriteGraph) else 'workflow_edit_graph'
    request_hash=digest({'tool':tool,'body':payload.model_dump(mode='json',exclude_unset=True)})
    async with service.control_lock,SessionLocal() as session:
        from ..runtime.models import RuntimeGate
        gate=await session.get(RuntimeGate,1)
        if gate and gate.closing: service.reject('WORKFLOW_WORLD_CLOSING')
        grant=None
        if execution_id:
            from .planning import authorized
            grant=await authorized(session,execution_id,tool)
            if (grant.run_id or grant.definition_id)!=target_id or ('run' if grant.run_id else 'definition')!=kind: service.reject('WORKFLOW_GRAPH_SCOPE',403)
        row,number,before=await target(session,cid,uid,kind,target_id)
        duplicate=await session.scalar(select(WorkflowGraphRevision).where(WorkflowGraphRevision.target_key==kind+':'+target_id,
            WorkflowGraphRevision.mutation_key==payload.mutation_key))
        if duplicate:
            if duplicate.request_digest!=request_hash: service.reject('WORKFLOW_GRAPH_MUTATION_CONFLICT')
            return {**revision_json(duplicate),'name':duplicate.changes_json.get('name',row.name if kind=='definition' else row.snapshot['name']),'committed':True,'replayed':True,'executable':not issues(duplicate.graph),'compile_issues':issues(duplicate.graph)}
        if number!=payload.expected_graph_revision: service.reject('WORKFLOW_REVISION_CONFLICT' if compatibility else 'WORKFLOW_GRAPH_REVISION_CONFLICT')
        if isinstance(payload,WriteGraph):
            if execution_id and number>0:
                from ..models import ExecutionAllocation
                allocation=await session.get(ExecutionAllocation,execution_id)
                if allocation.authority_json.get('complete_graph_revision')!=number: service.reject('WORKFLOW_GRAPH_READ_REQUIRED')
            raw=payload.graph.model_dump(mode='json',exclude_unset=True)
            if 'presentation' not in raw:
                raw['presentation'] = copy.deepcopy(before.get('presentation'))
                trim_presentation(raw)
            old={n['id']:n for n in before['nodes']}
            for n in raw.get('nodes',[]):
                for field in DISPLAY_FIELDS:
                    if field not in n and n['id'] in old: n[field]=old[n['id']].get(field)
        else: raw=apply_operations(before,payload.operations)
        graph=normalize(raw,payload.operations if isinstance(payload,EditGraph) else None)
        if not compatibility: await validate_authority(session,cid,uid,graph)
        else:
            conv=await service.owned(session,cid,uid)
            await service.validate_roles(session,conv,[{'kind':'role','role_id':n['role_id']} for n in graph['nodes'] if n.get('role_id') is not None],uid)
        validate_constraints(graph,grant.constraints_json if grant else {})
        if kind=='run': validate_constraints(graph,row.snapshot.get('constraints',{}))
        # 现有草稿的保护约束对前端/兼容 REST 同样生效，不能经另一入口绕过。
        from ..models import CoordinationSession
        owners=(await session.scalars(select(CoordinationSession).where(CoordinationSession.conversation_id==cid,
            CoordinationSession.definition_id==target_id if kind=='definition' else CoordinationSession.run_id==target_id,
            CoordinationSession.status.in_(['queued','running'])))).all()
        for owner in owners: validate_constraints(graph,owner.constraints_json)
        changes=diff(before,graph);compiled_issues=issues(graph)
        prepared=None
        if kind=='run':
            from .replanning import prepare
            prepared=await prepare(session,row,before,graph,target_is_effective=number==(row.graph_revision or 0))
        if payload.validate_only:
            return {'graph_revision':number,'graph':graph,'changes':changes,'validated':True,'committed':False,'executable':not compiled_issues,'compile_issues':compiled_issues}
        if row and not await session.scalar(select(WorkflowGraphRevision.id).where(WorkflowGraphRevision.target_key==kind+':'+target_id,
                WorkflowGraphRevision.number==number)):
            await record(session,cid=cid,uid=uid,kind=kind,target_id=target_id,number=number,graph=before,legacy=True)
        if kind=='definition':
            if row is None:
                row=WorkflowDefinition(id=target_id,conversation_id=cid,name='协调流程',revision=0,graph=graph,created_at=service.now(),updated_at=service.now())
                session.add(row)
            row.graph,row.revision,row.updated_at=graph,number+1,service.now()
            if isinstance(payload,WriteGraph) and payload.name:
                if not payload.name.strip(): problem('WORKFLOW_NODE_INVALID')
                row.name=payload.name.strip()
            await session.flush()
        changes['name']=row.name if kind=='definition' else row.snapshot['name']
        version=await record(session,cid=cid,uid=uid,kind=kind,target_id=target_id,number=number+1,graph=graph,
            mutation_key=payload.mutation_key,request_digest=request_hash,execution_id=execution_id,changes=changes)
        pending=[]
        if kind=='run':
            from .replanning import commit
            await commit(session,row,version,prepared)
            pending.append(await service.changed(session,row))
        pending.append(await events.append_event(session,cid,'workflow_graph_updated',
            {'definition_id':row.id if kind=='definition' else row.definition_id,'run_id':row.id if kind=='run' else None,
             'graph_revision':version.number,'mutation_id':version.id},revision=version.number))
        if execution_id:
            from ..models import ExecutionAllocation
            allocation=await session.get(ExecutionAllocation,execution_id)
            allocation.authority_json={**allocation.authority_json,'complete_graph_revision':version.number}
        await session.commit()
        logging.getLogger('roleplex.workflows').info('workflow.graph_committed',extra={'conversation_id':cid,'execution_id':execution_id,'mutation_id':version.id,'graph_revision':version.number,'target_kind':kind,'target_id':target_id,'added_count':len(changes['added']),'updated_count':len(changes['updated']),'removed_count':len(changes['removed']),'status':version.status})
        result={**revision_json(version),'replayed':False,'committed':True,'executable':not compiled_issues,'compile_issues':compiled_issues,
            'name':row.name if kind=='definition' else row.snapshot['name']}
    await events.publish_events(*pending)
    return result
