import { useState } from 'react'
import { ChevronDown } from 'lucide-react'
import type { Part } from '../api/client'
import { explorationSummary } from './exploration'
import { ToolCallCard } from './ToolCallCard'

/** 连续只读调用的展示容器；单项时不显示组标题，后续追加也不重挂载原工具卡。
 * @param parts 按调用开始顺序排列的只读工具，首项身份稳定。
 * @param conversationId 所属会话，复用原详情鉴权。
 * @param messageId 所属角色消息，不跨 execution 归组。
 * @param isOwner 当前账号是否允许主动查看私有详情。
 */
export function ExplorationGroup({ parts, conversationId, messageId, isOwner }: {
  parts: Part[]; conversationId: number; messageId: number; isOwner: boolean
}) {
  const [open, setOpen] = useState(true)
  const grouped = parts.length > 1
  const id = `exploration-${messageId}-${parts[0].call_id}`
  // 保留子组件挂载，主动折叠后追加/完成都不会重置用户选择或已展开详情。
  return <div data-testid={grouped ? 'exploration-group' : undefined}
    data-reading-anchor={`m-${messageId}:explore-${parts[0].call_id}`}
    className={grouped ? 'my-3 min-w-0 rounded-xl border border-slate-700/70 bg-slate-950/30 text-xs' : undefined}>
    {grouped && <button type="button" aria-label="探索记录" aria-expanded={open} aria-controls={id}
      onClick={() => setOpen(!open)}
      className="flex w-full items-start gap-2 rounded-xl px-3 py-2.5 text-left hover:bg-slate-800/50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-400">
      <ChevronDown size={14} className={`mt-0.5 shrink-0 text-slate-500 ${open ? 'rotate-180' : ''}`} />
      <span className="shrink-0 font-medium text-slate-300">探索</span>
      <span className="min-w-0 text-slate-400">{explorationSummary(parts)}</span>
    </button>}
    <div id={id} hidden={grouped && !open} className={grouped ? 'px-3' : undefined}>
      {parts.map((part) => <div key={part.call_id} data-reading-anchor={`m-${messageId}:tool-${part.call_id}`}>
        <ToolCallCard part={part} conversationId={conversationId} messageId={messageId} isOwner={isOwner} />
      </div>)}
    </div>
  </div>
}
