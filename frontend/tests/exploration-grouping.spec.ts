import { expect, test } from '@playwright/test'
import type { Message, Part } from '../src/api/client'
import { explorationSummary, groupMessageParts } from '../src/components/exploration'

/** 构造不含私有参数的时间线消息。
 * @param parts 原顺序消息段。
 * @param timelineVersion 历史位置是否可信。
 */
function message(parts: Part[], timelineVersion = 1): Message {
  return { id: 1, conversation_id: 1, sender_type: 'role', sender_id: 1, reply_to_id: null,
    mentions: [], parts_json: parts, status: 'completed', revision: 1, chain_id: null,
    created_at: '', timeline_version: timelineVersion }
}

/** 构造真实工具名对应的安全元数据。
 * @param id 调用身份。
 * @param name 工具名，不从命令或正文推断类型。
 */
function tool(id: string, name = 'workspace_read'): Part {
  return { type: 'tool_call', call_id: id, tool_name: name, status: 'success' }
}

test('只归并相邻原生 Read/List，保持原始对象、顺序和首调用锚点', () => {
  const parts = [tool('a'), tool('b', 'workspace_list'), { type: 'text', text: '准备修改' }, tool('c')]
  const groups = groupMessageParts(message(parts))
  expect(groups.map((group) => group.parts.length)).toEqual([2, 1, 1])
  expect(groups[0].kind).toBe('exploration')
  expect(groups[0].parts[0]).toBe(parts[0])
  expect(groups[0].key).toBe(groupMessageParts(message(parts.slice(0, 1)))[0].key)
  expect(groups.flatMap((group) => group.parts)).toEqual(parts)
})

test('正文、未知内容、写入、Shell、服务和缺失身份均阻断归组', () => {
  const boundaries: Part[] = [{ type: 'text', text: '' }, { type: 'future' }, tool('w', 'workspace_write'),
    tool('e', 'workspace_edit'), tool('s', 'workspace_run_shell'), tool('v', 'workspace_start_service'),
    tool('m', 'mcp_workspace_read'), tool('q', 'workspace_search'), tool('')]
  for (const boundary of boundaries) {
    const groups = groupMessageParts(message([tool('a'), boundary, tool('b')]))
    expect(groups.map((group) => group.parts.length)).toEqual([1, 1, 1])
  }
})

test('历史或未知版本、重复调用身份不猜测归组，失败只读项保留', () => {
  for (const version of [0, 2]) {
    expect(groupMessageParts(message([tool('a'), tool('b')], version)).every((group) => group.kind === 'part')).toBe(true)
  }
  expect(groupMessageParts(message([tool('a'), tool('a')])).every((group) => group.kind === 'part')).toBe(true)
  const failed = { ...tool('b'), status: 'rejected', error_code: 'WORKSPACE_FILE_NOT_FOUND' }
  expect(groupMessageParts(message([tool('a'), failed]))[0].parts).toEqual([tool('a'), failed])
})

test('折叠摘要不掩盖取消、中断或未知状态，不把调用次数称为文件数', () => {
  const statuses = ['running', 'failed', 'rejected', 'cancelled', 'interrupted', 'future', 'success']
  const summary = explorationSummary(statuses.map((status, index) => ({ ...tool(String(index)), status })))
  expect(summary).toBe('读取 7 次 · 1 项执行中 · 1 项失败 · 1 项已拒绝 · 1 项已取消 · 1 项已中断 · 1 项状态未知')
  expect(explorationSummary([tool('a'), tool('b', 'workspace_list')])).toBe('读取 1 次 · 列目录 1 次 · 已完成')
  expect(explorationSummary([{ ...tool('a'), error_code: 'EXECUTION_INTERRUPTED' }])).toContain('已中断')
})
