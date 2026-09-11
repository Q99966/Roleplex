import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react'
import { AlertCircle, Send, Square, X } from 'lucide-react'
import type { Role } from '../api/client'
import { useChatStore } from '../store/chat'

type Mention = number | 'all'
type Props = {
  conversationId: number
  group: boolean
  memberRoles: Role[]
  hasReplyRole: boolean
  onProcessCommand: () => void
}

/** 当前会话的多行草稿、显式 mentions 和统一提交入口，不持久化私有草稿。
 * @param props 会话身份、群聊成员和本地进程面板入口；切换会话由父组件重新挂载。
 */
export function ChatComposer({ conversationId, group, memberRoles, hasReplyRole, onProcessCommand }: Props) {
  const { loading, sending, generating, activeGenerationIds, subscription, sendMessage, stopGeneration } = useChatStore()
  const [draft, setDraft] = useState('')
  const [mentions, setMentions] = useState<Mention[]>([])
  const [cursor, setCursor] = useState({ start: 0, end: 0 })
  const [dismissed, setDismissed] = useState(false)
  const [choice, setChoice] = useState(0)
  const [composingView, setComposingView] = useState(false)
  const composing = useRef(false)
  const revision = useRef(0)
  const alive = useRef(false)
  const input = useRef<HTMLTextAreaElement>(null)
  const selectionAfterRender = useRef<number | null>(null)
  const listId = useId()
  const match = group && cursor.start === cursor.end ? draft.slice(0, cursor.start).match(/@([^\s@]*)$/) : null
  const query = match?.[1].toLocaleLowerCase() ?? ''
  const options: Array<{ target: Mention; label: string }> = [
    { target: 'all', label: '全部 · 按成员顺序回复' },
    ...memberRoles.filter((role) => role.active && !role.deleted_at && role.name.toLocaleLowerCase().includes(query))
      .map((role) => ({ target: role.id, label: role.name })),
  ]
  const showSuggestions = Boolean(match) && !dismissed && !composingView
  const selected = Math.min(choice, options.length - 1)
  const canSend = hasReplyRole && !loading && !sending && subscription === 'ready' && Boolean(draft.trim())

  useEffect(() => {
    alive.current = true
    return () => { alive.current = false }
  }, [])

  useLayoutEffect(() => {
    if (!showSuggestions) return
    const list = document.getElementById(listId)
    const option = document.getElementById(`${listId}-${selected}`)
    if (!list || !option) return
    // 只滚动补全列表，不能让方向键选择滚动外层聊天历史。
    const deltaTop = option.getBoundingClientRect().top - list.getBoundingClientRect().top
    if (deltaTop < 0) list.scrollTop += deltaTop
    else if (deltaTop + option.offsetHeight > list.clientHeight) list.scrollTop += deltaTop + option.offsetHeight - list.clientHeight
  }, [showSuggestions, selected, listId])

  useLayoutEffect(() => {
    const element = input.current
    if (!element) return
    /** 内容和可用宽度变化时按真实滚动高度调整，上限由 CSS 控制。 */
    function resize() {
      if (!element) return
      element.style.height = '0px'
      element.style.height = `${element.scrollHeight}px`
    }
    resize()
    if (selectionAfterRender.current !== null) {
      element.setSelectionRange(selectionAfterRender.current, selectionAfterRender.current)
      selectionAfterRender.current = null
    }
    let width = element.getBoundingClientRect().width
    const observer = new ResizeObserver(() => {
      const next = element.getBoundingClientRect().width
      if (next !== width) { width = next; resize() }
    })
    observer.observe(element)
    window.addEventListener('resize', resize)
    return () => { observer.disconnect(); window.removeEventListener('resize', resize) }
  }, [draft])

  /** 根据光标或选择区更新补全上下文，不改写正文。
   * @param element 当前输入元素，使用原生 UTF-16 光标位置与字符串切片一致。
   */
  function updateCursor(element: HTMLTextAreaElement) {
    const next = { start: element.selectionStart, end: element.selectionEnd }
    if (next.start !== cursor.start || next.end !== cursor.end) {
      setCursor(next)
      setDismissed(false)
      setChoice(0)
    }
  }

  /** 原地替换当前 @ 片段，保留光标后内容并登记显式角色身份。
   * @param target 用户通过键盘或鼠标确认的角色/all。
   */
  function selectMention(target: Mention) {
    if (!match || match.index === undefined) return
    const label = target === 'all' ? '全部' : memberRoles.find((role) => role.id === target)?.name
    if (!label) return
    const inserted = `@${label} `
    const position = match.index + inserted.length
    revision.current++
    setDraft(draft.slice(0, match.index) + inserted + draft.slice(cursor.start))
    setMentions((current) => target === 'all' ? ['all'] : current.includes('all') || current.includes(target) ? current : [...current, target])
    setCursor({ start: position, end: position })
    setDismissed(true)
    selectionAfterRender.current = position
    input.current?.focus()
  }

  /** 仅接收明确提交；确认成功且草稿未改动才清理，不自动重发未知结果。 */
  async function submit() {
    if (composing.current || useChatStore.getState().conversationId !== conversationId) return
    if (draft.trim() === '/ps') {
      revision.current++
      setDraft(''); setMentions([]); setCursor({ start: 0, end: 0 })
      onProcessCommand()
      return
    }
    if (!canSend) return
    const submittedRevision = revision.current
    const accepted = await sendMessage(draft, mentions)
    if (accepted && alive.current && submittedRevision === revision.current) {
      revision.current++
      setDraft(''); setMentions([]); setCursor({ start: 0, end: 0 }); setDismissed(false)
    }
  }

  /** 输入法优先于补全，补全优先于发送；软键盘 Enter 保留为原生换行。
   * @param event 输入框的键盘事件，229 兼容组合结束先于确认键的浏览器。
   */
  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (composing.current || event.nativeEvent.isComposing || event.keyCode === 229) return
    if (event.key === 'Enter' && event.shiftKey) return
    if (event.ctrlKey || event.altKey || event.metaKey) return
    if (showSuggestions && ['ArrowDown', 'ArrowUp'].includes(event.key)) {
      event.preventDefault()
      setChoice((current) => (Math.min(current, options.length - 1) + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length)
      return
    }
    if (showSuggestions && event.key === 'Escape') { event.preventDefault(); setDismissed(true); return }
    if (event.key !== 'Enter') return
    // 触屏外接物理键盘仍可 Enter 发送；软键盘通常不提供物理 code。
    if (window.matchMedia('(pointer: coarse)').matches && !event.code) return
    event.preventDefault()
    if (event.repeat) return
    if (showSuggestions) selectMention(options[selected].target)
    else void submit()
  }

  return <form onSubmit={(event) => { event.preventDefault(); void submit() }} className="shrink-0 border-t border-slate-800 bg-slate-900 p-3 sm:p-4">
    {!hasReplyRole && <div className="mb-2 flex items-center gap-2 rounded-lg border border-amber-900/50 bg-amber-950/30 px-3 py-2 text-xs text-amber-300">
      <AlertCircle size={13} className="shrink-0" /><span>角色已删除，当前会话仅可查看历史消息。</span>
    </div>}
    {group && <div className="mb-2 flex min-h-6 flex-wrap items-center gap-1.5">
      {mentions.map((target) => {
        const label = target === 'all' ? '全部' : memberRoles.find((role) => role.id === target)?.name
        return label ? <span key={target} className="flex items-center gap-1 rounded-full border border-indigo-500/30 bg-indigo-950/40 px-2 py-1 text-xs text-indigo-300">
          @{label}<button type="button" aria-label={`移除 @${label}`} className="rounded p-1 focus-visible:outline focus-visible:outline-indigo-400"
            onClick={() => { revision.current++; setMentions((current) => current.filter((item) => item !== target)) }}><X size={12} /></button>
        </span> : null
      })}
      {!mentions.length && <span className="text-xs text-slate-400">无 @ 时消息只记录，不触发 Agent。</span>}
    </div>}
    <div className="relative rounded-xl border border-slate-800 bg-slate-950/80 p-2.5 focus-within:border-indigo-400">
      {showSuggestions && <div id={listId} role="listbox" aria-label="@ 角色补全" className="absolute bottom-full left-0 z-20 mb-2 max-h-48 w-72 max-w-full overflow-y-auto rounded-xl border border-slate-700 bg-slate-900 shadow-panel">
        {options.map((option, index) => <button key={option.target} id={`${listId}-${index}`} type="button" role="option"
          aria-selected={index === selected} onMouseDown={(event) => event.preventDefault()} onClick={() => selectMention(option.target)}
          className={`block w-full break-words px-3 py-3 text-left text-xs text-indigo-200 focus-visible:outline focus-visible:outline-indigo-400 ${index === selected ? 'bg-slate-800' : 'hover:bg-slate-800/60'}`}>
          @{option.label}
        </button>)}
      </div>}
      <div className="flex items-end gap-2">
        <textarea ref={input} rows={1} value={draft} disabled={!hasReplyRole} aria-label="消息输入框"
          aria-controls={showSuggestions ? listId : undefined} aria-activedescendant={showSuggestions ? `${listId}-${selected}` : undefined}
          aria-autocomplete={group ? 'list' : undefined}
          placeholder={hasReplyRole ? (group ? '输入 @ 选择回复角色…' : '输入消息…') : '角色已删除，无法继续发送'}
          onKeyDown={onKeyDown} onSelect={(event) => updateCursor(event.currentTarget)}
          onCompositionStart={() => { composing.current = true; setComposingView(true) }}
          onCompositionEnd={() => { composing.current = false; setComposingView(false) }}
          onChange={(event) => {
            revision.current++; setDraft(event.target.value); updateCursor(event.currentTarget); setChoice(0); setDismissed(false)
            if (!event.target.value.trim()) setMentions([])
          }}
          className="min-h-11 max-h-[min(240px,30dvh)] w-full min-w-0 resize-none overflow-y-auto bg-transparent py-2.5 text-sm leading-6 text-slate-200 outline-none placeholder:text-slate-400 disabled:cursor-not-allowed disabled:text-slate-500"
        />
        {generating ? <button type="button" onClick={() => void stopGeneration()} className="min-h-11 shrink-0 rounded-lg bg-slate-800 px-3 py-2 text-xs text-slate-200 hover:bg-slate-700 focus-visible:outline focus-visible:outline-indigo-400">
          <Square size={12} className="mr-1 inline" />{group ? '停止整条链' : '停止生成'}{group && activeGenerationIds.length > 1 ? ` · ${activeGenerationIds.length}` : ''}
        </button> : <button type="submit" disabled={!canSend} aria-label="发送消息" className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-indigo-600 text-white hover:bg-indigo-500 focus-visible:outline focus-visible:outline-indigo-300 disabled:opacity-40 disabled:cursor-not-allowed">
          <Send size={16} />
        </button>}
      </div>
    </div>
  </form>
}
