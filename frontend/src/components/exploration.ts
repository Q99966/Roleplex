import type { Message, Part } from '../api/client'

export type PartGroup = { kind: 'part' | 'exploration'; key: string; index: number; parts: Part[] }
const READ_TOOLS = new Set(['workspace_read', 'workspace_list'])

/** 只将同一消息中连续的原生只读调用归为展示段，不改变执行事实或创建批量调用。
 * @param message 服务端完整有序消息；旧版、未知版及重复调用身份保守逐项展示。
 */
export function groupMessageParts(message: Message): PartGroup[] {
  const counts = new Map<string, number>()
  for (const part of message.parts_json) {
    if (part.type === 'tool_call' && part.call_id) counts.set(part.call_id, (counts.get(part.call_id) ?? 0) + 1)
  }
  const groups: PartGroup[] = []
  message.parts_json.forEach((part, index) => {
    // 当前协议一条角色消息对应一次 generation/execution；绝不跨消息合组。
    const eligible = message.sender_type === 'role' && message.timeline_version === 1
      && part.type === 'tool_call' && READ_TOOLS.has(part.tool_name ?? '')
      && Boolean(part.call_id) && counts.get(part.call_id!) === 1
    const previous = groups.at(-1)
    if (eligible && previous?.kind === 'exploration') previous.parts.push(part)
    else groups.push({ kind: eligible ? 'exploration' : 'part', index,
      key: part.call_id && counts.get(part.call_id) === 1 ? `tool-${part.call_id}` : `part-${part.part_id ?? index}`, parts: [part] })
  })
  return groups
}

/** 用调用次数及各项状态概括探索过程，不将调用次数冒充文件数或推断整组成功。
 * @param parts 本组只读调用的安全元数据，不读取任何私有参数。
 */
export function explorationSummary(parts: Part[]): string {
  const labels: Record<string, string> = { running: '执行中', failed: '失败', rejected: '已拒绝',
    cancelled: '已取消', interrupted: '已中断', unknown: '状态未知' }
  const reads = parts.filter((part) => part.tool_name === 'workspace_read').length
  const lists = parts.length - reads
  const operations = [reads && `读取 ${reads} 次`, lists && `列目录 ${lists} 次`].filter(Boolean)
  const states = parts.map((part) => part.error_code === 'EXECUTION_INTERRUPTED' ? 'interrupted'
    : part.status === 'success' ? 'success' : part.status && labels[part.status] ? part.status : 'unknown')
  const counts = Object.entries(labels).map(([state, label]) => {
    const count = states.filter((value) => value === state).length
    return count ? `${count} 项${label}` : ''
  }).filter(Boolean)
  return [...operations, ...(counts.length ? counts : ['已完成'])].join(' · ')
}
