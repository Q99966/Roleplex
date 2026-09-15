import { useEffect, useId, useRef, useState } from 'react'
import { ChevronDown } from 'lucide-react'

type ToolCategoryProps = {
  label: string
  tools: readonly (readonly [string, string])[]
  selected: readonly string[]
  onToggle: (tool: string) => void
  onSelectAll: (checked: boolean) => void
}

/** 可折叠的工具类别；组复选框仅操作本类别，折叠不改变授权选择。
 * @param label 类别中文名称。
 * @param tools 本类别的工具名和用途。
 * @param selected 当前角色选中的全部工具。
 * @param onToggle 切换单个工具的表单状态。
 * @param onSelectAll 勾选或取消本类别全部工具。
 */
export function ToolCategory({ label, tools, selected, onToggle, onSelectAll }: ToolCategoryProps) {
  const [expanded, setExpanded] = useState(true)
  const controlsId = useId()
  const checkbox = useRef<HTMLInputElement>(null)
  const count = tools.filter(([tool]) => selected.includes(tool)).length
  const all = count === tools.length
  const partial = count > 0 && !all
  useEffect(() => {
    if (checkbox.current) checkbox.current.indeterminate = partial
  }, [partial])

  return <fieldset className="w-full min-w-0 rounded-xl border border-slate-800">
    <legend className="sr-only">{label}</legend>
    <div className="flex items-center gap-3 px-3 py-2">
      <button type="button" aria-label={`${expanded ? '折叠' : '展开'}${label}`} aria-expanded={expanded} aria-controls={controlsId}
        onClick={() => setExpanded(value => !value)}
        className="flex min-w-0 flex-1 items-center gap-2 rounded text-left text-xs text-slate-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-500">
        <ChevronDown size={14} aria-hidden="true" className={`shrink-0 transition-transform ${expanded ? '' : '-rotate-90'}`} />
        <span>{label}</span><span className="text-[10px] text-slate-500">{count}/{tools.length}</span>
      </button>
      <label className="flex shrink-0 cursor-pointer items-center gap-1.5 text-[10px] text-slate-400">
        <input ref={checkbox} type="checkbox" aria-label={`${label}全选`} checked={all} aria-checked={partial ? 'mixed' : all}
          onChange={event => onSelectAll(event.target.checked)} className="h-3.5 w-3.5 accent-indigo-600" />
        全选
      </label>
    </div>
    <div id={controlsId} hidden={!expanded} className="px-3 pb-3">
      <div className="flex flex-wrap gap-2">{tools.map(([tool, name]) => <button key={tool} type="button"
        aria-pressed={selected.includes(tool)} onClick={() => onToggle(tool)}
        className={`max-w-full break-words px-3 py-1.5 rounded-lg border text-[10px] font-medium transition ${selected.includes(tool)
          ? 'bg-indigo-600 border-indigo-500 text-white' : 'bg-slate-950 border-slate-800 text-slate-400 hover:text-slate-200'}`}>
        {name} ({tool})
      </button>)}</div>
    </div>
  </fieldset>
}
