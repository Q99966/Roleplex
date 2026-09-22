const labels: Record<string, string> = { facts: '已记录事项', open_items: '未完成事项', conflicts: '分歧与冲突', inferences: '推断与建议', execution_facts: '服务器执行记录' }

/** 用业务分类呈现摘要，保留来源编号；原始模型格式按需展开。 */
export function SummaryBody({ text }: { text: string }) {
  let value: Record<string, { text: string; sources?: number[] }[]>
  try {
    value = JSON.parse(text.slice(text.indexOf('{')))
    if (!value || !Array.isArray(value.facts)) throw new Error('unknown format')
  } catch { return <p className="whitespace-pre-wrap break-words leading-relaxed">{text}</p> }
  return <div className="space-y-3">
    {Object.entries(labels).map(([key, label]) => {
      const items = value[key]
      if (!Array.isArray(items) || !items.length) return null
      return <section key={key}><h5 className="font-medium">{label}</h5><ul className="mt-2 space-y-2">{items.map((item, index) => <li key={index} className="border-l-2 border-indigo-100 pl-2">
        <p className="whitespace-pre-wrap break-words leading-relaxed">{key === 'execution_facts' ? factDescription(item.text) : item.text}</p>
        <p className="mt-1 text-[10px] text-slate-500">来源消息：{item.sources?.map(id => `#${id}`).join('、') || '参见原文引用'}</p>
      </li>)}</ul></section>
    })}
    <details className="text-slate-500"><summary className="cursor-pointer">查看原始摘要格式</summary><pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-words font-sans">{text}</pre></details>
  </div>
}

function factDescription(text: string) {
  try {
    const value = JSON.parse(text) as { status?: string; tools?: Record<string, { effects?: Record<string, number>; confirmed_applied_items?: number }> }
    const status: Record<string, string> = { done: '回复已收口', stopped: '回复已停止', error: '回复失败', interrupted: '回复中断' }
    const details = Object.entries(value.tools ?? {}).map(([name, counts]) => counts.effects?.not_applicable
      ? `${name}：只读调用 ${counts.effects.not_applicable} 次`
      : `${name}：确认提交 ${counts.confirmed_applied_items ?? 0} 项${counts.effects?.unknown ? `，另有 ${counts.effects.unknown} 次副作用未知` : ''}`)
    return [status[value.status ?? ''] ?? '执行状态待核对', ...details, '这些是历史记录，不代表任务验收或当前文件仍未变化。'].join('。')
  } catch { return text }
}
