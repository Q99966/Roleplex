"""v2 有向流程语义：DAG 就绪调度与显式、互不嵌套的循环域。"""
from fastapi import HTTPException


def invalid(code):
    raise HTTPException(422, code)


def compile_graph(graph: dict) -> dict:
    """启动前编译完整语义；不从旧环路、布局或节点正文推测条件。"""
    nodes = graph['nodes']
    by_id = {n['id']: n for n in nodes}
    if any(nid.startswith('__') for nid in by_id):
        invalid('WORKFLOW_NODE_INVALID')
    edges = [tuple(edge) for edge in graph['edges']]
    rules = {}
    for rule in graph.get('edge_rules', []):
        key = (rule['source'], rule['target'])
        if key not in edges or key in rules:
            invalid('WORKFLOW_EDGE_CONFIG_INVALID')
        rules[key] = rule['when']
    loops, covered, back = {}, set(), set()
    for loop in graph.get('loops', []):
        body = set(loop['body'])
        if (loop['id'] in loops or len(body) != len(loop['body']) or not body.issubset(by_id)
            or covered & body or loop['entry'] not in body or loop['decision'] not in body
            or loop['exit'] in body or loop['exit'] not in by_id or not set(loop['carry_inputs']).issubset(body)):
            invalid('WORKFLOW_LOOP_CONFIG_INVALID')
        if not any(by_id[n]['kind'] in ('role', 'judge', 'approval') for n in body):
            invalid('WORKFLOW_LOOP_NO_PROGRESS')
        if by_id[loop['decision']]['kind'] not in ('condition', 'judge'):
            invalid('WORKFLOW_LOOP_CONFIG_INVALID')
        for source, target in edges:
            if source not in body and target in body and target != loop['entry']:
                invalid('WORKFLOW_LOOP_CONFIG_INVALID')
            if source in body and target not in body and (source, target) != (loop['decision'], loop['exit']):
                invalid('WORKFLOW_LOOP_CONFIG_INVALID')
        repeat = (loop['decision'], loop['entry'])
        leave = (loop['decision'], loop['exit'])
        if repeat not in edges or leave not in edges:
            invalid('WORKFLOW_LOOP_CONFIG_INVALID')
        for edge, value in [(repeat, loop['repeat_when']), (leave, not loop['repeat_when'])]:
            outcome = 'true' if value else 'false'
            if edge in rules and rules[edge] != outcome:
                invalid('WORKFLOW_LOOP_CONFIG_INVALID')
            rules[edge] = outcome
        back.add(repeat); covered |= body; loops[loop['id']] = loop
    incoming = {nid: [] for nid in by_id}
    outgoing = {nid: [] for nid in by_id}
    for source, target in edges:
        if (source, target) in back:
            continue
        incoming[target].append(source); outgoing[source].append(target)
    degree = {nid: len(values) for nid, values in incoming.items()}
    queue = [nid for nid in by_id if degree[nid] == 0]
    roots, order = list(queue), []
    while queue:
        nid = queue.pop(0); order.append(nid)
        for target in outgoing[nid]:
            degree[target] -= 1
            if not degree[target]: queue.append(target)
    if len(order) != len(nodes):
        invalid('WORKFLOW_LOOP_CONFIG_REQUIRED')
    entries = graph.get('entries') or (roots if len(roots) == 1 else [])
    if set(entries) != set(roots) or len(entries) != len(set(entries)):
        invalid('WORKFLOW_ENTRY_REQUIRED')
    ancestors = {}
    for nid in order:
        ancestors[nid] = set(incoming[nid]) | {x for parent in incoming[nid] for x in ancestors[parent]}
    for nid, node in by_id.items():
        if not set(node['inputs']).issubset(ancestors[nid]):
            invalid('WORKFLOW_INPUT_UNAVAILABLE')
        condition = node.get('condition')
        if node['kind'] in ('condition', 'judge'):
            if not condition or any(source != '$self' and source not in ancestors[nid] for source in condition['sources']):
                invalid('WORKFLOW_CONDITION_INVALID')
            if '$self' in condition['sources'] and node['kind'] != 'judge':
                invalid('WORKFLOW_CONDITION_INVALID')
            outcomes = {rules.get(edge) for edge in edges if edge[0] == nid}
            if outcomes != {'true', 'false'}:
                invalid('WORKFLOW_EDGE_CONFIG_INVALID')
        elif condition:
            invalid('WORKFLOW_CONDITION_INVALID')
        for target in [b for a, b in edges if a == nid]:
            kind = rules.get((nid, target), 'always')
            if node['kind'] not in ('condition', 'judge') and kind != 'always':
                invalid('WORKFLOW_EDGE_CONFIG_INVALID')
    for loop in loops.values():
        body = set(loop['body'])
        if any(nid != loop['entry'] and loop['entry'] not in ancestors[nid] for nid in body):
            invalid('WORKFLOW_LOOP_CONFIG_INVALID')
        if not (body - {loop['decision']}).issubset(ancestors[loop['decision']]):
            invalid('WORKFLOW_LOOP_CONFIG_INVALID')
    return {**graph, 'runtime_version': 2, 'entries': entries,
        'edge_rules': [{'source': a, 'target': b, 'when': rules.get((a, b), 'always')} for a, b in edges],
        'order': order, 'incoming': incoming, 'outgoing': outgoing,
        'loop_membership': {nid: lid for lid, loop in loops.items() for nid in loop['body']}}


def descendants(graph, node_id):
    """沿真实依赖（去掉回边）失效，保留无依赖的并行分支。"""
    seen, pending = set(), list(graph['outgoing'].get(node_id, []))
    while pending:
        nid = pending.pop()
        if nid in seen: continue
        seen.add(nid); pending.extend(graph['outgoing'].get(nid, []))
    return seen


def evaluate(condition, results):
    """只对存在的结构化标量做等值比较；缺失/不一致结果不默认循环。"""
    values = []
    for source in condition['sources']:
        result = results.get(source)
        if not isinstance(result, dict) or condition['key'] not in result:
            invalid('WORKFLOW_CONDITION_INVALID')
        value, expected = result[condition['key']], condition['value']
        numeric = type(value) in (int, float) and type(expected) in (int, float)
        if type(value) is not type(expected) and not numeric:
            invalid('WORKFLOW_CONDITION_INVALID')
        equal = value == expected
        values.append(equal if condition['operator'] == 'eq' else not equal)
    if not values: invalid('WORKFLOW_CONDITION_INVALID')
    return all(values) if condition['aggregate'] == 'all' else any(values)
