import { useEffect, useRef, useState } from 'react'
import { Layers3 } from 'lucide-react'
import { getAuthEpoch, type Conversation, type Role } from '../../api/client'
import { promptSettings, type PromptPreview, type WorldPromptSettings, type WorldPromptValues, type ConversationPromptValues } from '../../api/prompts'
import { useAppStore } from '../../store/app'
import { usePromptEditor } from './usePromptEditor'

const panel = 'rounded-2xl border border-slate-800 bg-panel p-4'
const field = 'mt-2 block w-full min-w-0 rounded-xl border border-slate-700 bg-slate-950/40 p-3 text-sm text-slate-200 outline-none focus:border-indigo-400'
const button = 'rounded-lg border border-slate-700 px-3 py-2 text-xs disabled:opacity-40'
const primary = 'rounded-lg bg-indigo-600 px-4 py-2 text-sm text-white disabled:opacity-40'
const worldValues = (record: WorldPromptSettings): WorldPromptValues => ({ platform_override: record.platform_override, world_prompt: record.world_prompt })
const sourceLabel: Record<string, string> = { runtime: '软件固定规则', default: '平台默认', override: '当前世界覆盖', world: '当前世界', role: '角色配置', inline: '已有技能配置', conversation: '当前会话' }

/** 世界 Owner 编辑平台覆盖与世界背景；空覆盖、默认继承和未保存输入分别表示。 */
export function WorldPromptSettingsPanel() {
  const { user, worldName } = useAppStore()
  if (!user?.is_owner) return <p>提示词配置由 Owner 管理。</p>
  return <WorldEditor key={`${getAuthEpoch()}:${worldName}:${user.id}`} scope={`${worldName}:${user.id}:world`} />
}

function WorldEditor({ scope }: { scope: string }) {
  const input = useRef<HTMLTextAreaElement>(null), status = useRef<HTMLParagraphElement>(null)
  const editor = usePromptEditor(scope, { read: promptSettings.world, write: promptSettings.saveWorld, values: worldValues,
    onSaved: () => { status.current?.focus(); window.dispatchEvent(new Event('roleplex:prompts-updated')) } })
  const value = editor.editing
  if (!value) return <div role="status">{editor.error || '正在读取提示词配置…'}{editor.error && <button className={`${button} ml-2`} onClick={() => void editor.refresh()}>重试读取</button>}</div>
  const { draft, latest, baseline } = value
  return <section aria-label="世界提示词配置" className="space-y-5 text-sm text-slate-300">
    <div className="flex items-center gap-3 border-b border-slate-800 pb-4"><Layers3 size={20} className="text-indigo-500" /><div>
      <h4 className="font-semibold">提示词与规则</h4><p className="mt-1 text-xs text-slate-500">当前世界：{latest.world_name} · 仅影响这个世界后续组装的输入</p></div></div>
    <form onSubmit={event => { event.preventDefault(); void editor.save() }} className="space-y-4">
      <fieldset disabled={editor.busy} className="space-y-4">
        <div className={panel}>
          <div className="flex flex-wrap items-center justify-between gap-3"><h5 className="font-semibold">平台协作规则</h5><span className="text-xs text-slate-500">默认模板 {latest.template_version}</span></div>
          <p className="mt-2 text-xs leading-relaxed text-slate-500">设置通用的协作与输出要求。保留默认，或为当前世界设置覆盖。</p>
          <label className="mt-3 flex items-center gap-2 text-xs"><input type="checkbox" checked={draft.platform_override !== null}
            onChange={event => editor.change({ ...draft, platform_override: event.target.checked ? latest.platform_default : null })} />自定义平台规则</label>
          <textarea aria-label="平台协作规则" rows={4} maxLength={100000} disabled={draft.platform_override === null} className={field}
            value={draft.platform_override ?? latest.platform_default} onChange={event => editor.change({ ...draft, platform_override: event.target.value })} />
          <div className="mt-3 flex flex-wrap items-center gap-3"><button type="button" className={button} disabled={draft.platform_override === null} onClick={() => { editor.change({ ...draft, platform_override: null }); input.current?.focus() }}>恢复平台默认</button>
            <span className="text-xs text-slate-500">{draft.platform_override === null ? '继承平台默认模板' : draft.platform_override === '' ? '已选择空覆盖，保存后省略此层' : '使用当前世界的自定义覆盖'}</span></div>
        </div>
        <label className={`block ${panel}`}><span className="font-semibold">世界系统提示词</span><span className="mt-2 block text-xs leading-relaxed text-slate-500">世界背景、公共目标和长期约定；与平台及角色规则一同提供。</span>
          <textarea ref={input} aria-label="世界系统提示词" rows={5} maxLength={100000} className={field} value={draft.world_prompt}
            onChange={event => editor.change({ ...draft, world_prompt: event.target.value })} placeholder="例如：这个世界的协作背景与共同目标" /></label>
      </fieldset>
      {editor.error && <p role="alert" className="text-xs text-red-400">{editor.error}</p>}
      {editor.conflict && <div className="rounded-xl border border-amber-400 p-3 text-xs">
        <p>服务端已有版本 {latest.revision}；你的输入基于版本 {baseline.revision}。</p>
        <details className="mt-2"><summary className="cursor-pointer">查看服务端新版本</summary><p className="mt-2 whitespace-pre-wrap break-words">平台：{latest.platform_override ?? '继承默认'}</p><p className="mt-2 whitespace-pre-wrap break-words">世界：{latest.world_prompt || '空'}</p></details>
        <button type="button" className={`${button} mt-3`} onClick={() => { editor.adopt(); input.current?.focus() }}>采用服务端配置</button>
      </div>}
      <div className="flex flex-wrap items-center gap-3"><button type="submit" className={primary} disabled={editor.busy || !editor.dirty || editor.conflict}>保存世界提示词</button>
        <button type="button" className={button} disabled={editor.busy} onClick={() => void editor.refresh()}>刷新配置</button>
        <p ref={status} tabIndex={-1} role="status" className="text-xs text-slate-500 outline-none">{editor.busy ? '正在保存…' : editor.dirty ? '有未保存修改' : `已保存 · 世界版本 ${baseline.revision}`}</p></div>
    </form>
    <details className="text-xs text-slate-500"><summary className="cursor-pointer">平台固定执行规则</summary><p className="mt-2 leading-relaxed">{latest.runtime_rules}</p><p className="mt-2">修改提示词不会变更工具授权或人工审批。</p></details>
  </section>
}

/** 会话上下文入口的第一批：配置会话提示词并核对当前角色的生效来源。 */
export function ConversationPromptSettings({ conversation }: { conversation: Conversation }) {
  const { user, worldName } = useAppStore()
  if (!user?.is_owner) return <p className="text-xs text-slate-500">提示词配置由 Owner 管理。</p>
  return <ConversationEditor key={`${getAuthEpoch()}:${worldName}:${user.id}:${conversation.id}`} conversation={conversation} scope={`${worldName}:${user.id}:conversation:${conversation.id}`} />
}

function ConversationEditor({ conversation, scope }: { conversation: Conversation; scope: string }) {
  const input = useRef<HTMLTextAreaElement>(null), status = useRef<HTMLParagraphElement>(null)
  const roles = useAppStore(state => state.roles).filter(role => conversation.role_ids.includes(role.id) && role.active && !role.deleted_at)
  const [roleId, setRoleId] = useState(conversation.role_ids[0] ?? 0)
  const selected = roles.find(role => role.id === roleId) ?? roles[0]
  const [refresh, setRefresh] = useState(0)
  useEffect(() => {
    const updated = () => setRefresh(value => value + 1)
    window.addEventListener('roleplex:prompts-updated', updated)
    return () => window.removeEventListener('roleplex:prompts-updated', updated)
  }, [])
  const editor = usePromptEditor(scope, {
    read: signal => promptSettings.conversation(conversation.id, signal),
    write: (values: ConversationPromptValues, revision) => promptSettings.saveConversation(conversation.id, values, revision),
    values: record => ({ prompt: record.prompt }), onSaved: () => { status.current?.focus(); setRefresh(value => value + 1) },
  })
  const value = editor.editing
  if (!value) return <p role="status" className="text-xs">{editor.error || '正在读取会话提示词…'}{editor.error && <button className={`${button} ml-2`} onClick={() => void editor.refresh()}>重试读取</button>}</p>
  return <div className="space-y-5 text-xs text-slate-300">
    <section aria-label="会话提示词配置" className="space-y-3">
      <h4 className="text-sm font-semibold">会话提示词</h4><p className="leading-relaxed text-slate-500">为“{conversation.title}”补充共同约定。保存后用于后续输入，已有消息保留。</p>
      <form className="space-y-3" onSubmit={event => { event.preventDefault(); void editor.save() }}>
        <textarea ref={input} aria-label="会话提示词" rows={6} maxLength={100000} className={field} disabled={editor.busy} value={value.draft.prompt}
          onChange={event => editor.change({ prompt: event.target.value })} placeholder="这个会话的目标、背景与协作约定" />
        {editor.error && <p role="alert" className="text-red-400">{editor.error}</p>}
        {editor.conflict && <div className="rounded-xl border border-amber-400 p-3">
          <p>配置已更新到版本 {value.latest.revision}，未提交输入仍保留。</p>
          <details className="mt-2"><summary className="cursor-pointer">查看服务端新版本</summary><p className="mt-2 whitespace-pre-wrap break-words">{value.latest.prompt || '空提示词'}</p></details>
          <button type="button" className={`${button} mt-3`} onClick={() => { editor.adopt(); input.current?.focus() }}>采用服务端配置</button>
        </div>}
        <div className="flex flex-wrap gap-2"><button type="submit" className={primary} disabled={editor.busy || !editor.dirty || editor.conflict}>保存会话提示词</button>
          <button type="button" className={button} disabled={editor.busy} onClick={() => void editor.refresh()}>刷新配置</button></div>
        <p ref={status} tabIndex={-1} role="status" className="text-slate-500 outline-none">{editor.busy ? '正在保存…' : editor.dirty ? '有未保存修改' : `已保存 · 会话版本 ${value.baseline.revision}`}</p>
      </form>
    </section>
    <section className="space-y-3 border-t border-slate-800 pt-4">
      <div className="flex flex-wrap items-center justify-between gap-2"><h4 className="text-sm font-semibold">生效来源</h4><button type="button" className="text-indigo-500" onClick={() => window.dispatchEvent(new Event('roleplex:open-prompt-settings'))}>世界规则设置</button></div>
      <label className="block">预览角色<select aria-label="预览角色" className={field} value={selected?.id ?? ''} onChange={event => setRoleId(Number(event.target.value))}>
        {!roles.length && <option value="">没有可用角色</option>}{roles.map(role => <option key={role.id} value={role.id}>{role.name}</option>)}</select></label>
      <button type="button" className={button} disabled={!selected} onClick={() => setRefresh(value => value + 1)}>刷新生效来源</button>
      {selected && <PromptSources conversationId={conversation.id} role={selected} refresh={refresh} />}
    </section>
  </div>
}

function PromptSources({ conversationId, role, refresh }: { conversationId: number; role: Role; refresh: number }) {
  const [value, setValue] = useState<PromptPreview | null>(null), [error, setError] = useState('')
  useEffect(() => {
    const controller = new AbortController(), epoch = getAuthEpoch()
    setValue(null); setError('')
    void promptSettings.preview(conversationId, role.id, controller.signal).then(result => {
      if (!controller.signal.aborted && epoch === getAuthEpoch()) setValue(result)
    }).catch(() => { if (!controller.signal.aborted && epoch === getAuthEpoch()) setError('生效来源读取失败，请刷新重试。') })
    return () => controller.abort()
  }, [conversationId, role.id, role.revision, refresh])
  if (!value) return <p className="text-slate-500">{error || '正在核对来源…'}</p>
  const unavailable = value.configured_tools.filter(name => !value.capabilities.tools.some(tool => tool.name === name))
  return <section aria-label="提示词生效来源" className="space-y-3">
    <p className="leading-relaxed text-slate-500">{value.role_name}的当前已保存配置，供后续组装使用。工作流节点另按本次任务分配；这里不包含聊天历史。</p>
    <ol className="space-y-2">{value.layers.map((layer, index) => <li key={layer.key}><details className="rounded-xl border border-slate-800 bg-panel p-3">
      <summary className="cursor-pointer"><span className="font-medium">{index + 1}. {layer.title}</span><span className="mt-1 block text-[11px] text-slate-500">{sourceLabel[layer.source] ?? layer.source} · 版本 {layer.revision} · {layer.characters} 字符</span></summary>
      <p className="mt-3 whitespace-pre-wrap break-words leading-relaxed">{layer.text || '此层为空'}</p>
    </details></li>)}</ol>
    <details className="rounded-xl border border-slate-800 p-3"><summary className="cursor-pointer">当前可用工具 · {value.capabilities.tools.length}</summary>
      <ul className="mt-2 space-y-2">{value.capabilities.tools.map(tool => <li key={tool.name}><p className="break-all font-medium">{tool.name}</p><p className="text-slate-500">{tool.source === 'workspace' ? '工作区能力' : '任务控制能力'} · {tool.danger === 'safe' ? '安全白名单' : '按权限执行'}</p></li>)}</ul>
      {!!unavailable.length && <p className="mt-3 break-words text-slate-500">角色已配置，但当前会话不可用：{unavailable.join('、')}</p>}
    </details>
    {value.latest_execution && <details className="rounded-xl border border-slate-800 p-3"><summary className="cursor-pointer">最近实际采用</summary>
      <p className="mt-2 text-slate-500">世界版本 {value.latest_execution.snapshot.revisions.world} · 角色版本 {value.latest_execution.snapshot.revisions.role} · 会话版本 {value.latest_execution.snapshot.revisions.conversation}</p>
      <p className="mt-2 text-slate-500">这次执行的来源记录保持不变；新配置由后续请求采用。</p>
    </details>}
  </section>
}

/** 角色编辑只按需查看继承规则，避免把世界规则复制进每个角色的正文。 */
export function RolePromptInheritance() {
  const [value, setValue] = useState<WorldPromptSettings | null>(null), [error, setError] = useState('')
  const [open, setOpen] = useState(false)
  useEffect(() => {
    if (!open) return
    const controller = new AbortController(), epoch = getAuthEpoch()
    void promptSettings.world(controller.signal).then(result => { if (!controller.signal.aborted && epoch === getAuthEpoch()) setValue(result) })
      .catch(() => { if (!controller.signal.aborted && epoch === getAuthEpoch()) setError('继承规则读取失败，重新展开可重试。') })
    return () => controller.abort()
  }, [open])
  return <details className="mt-3 text-xs text-slate-500" onToggle={event => { setError(''); setOpen(event.currentTarget.open) }}><summary className="cursor-pointer">查看继承的平台与世界规则</summary>
    {value ? <div className="mt-3 space-y-3"><p>世界 {value.world_name} · 配置版本 {value.revision}</p>
      <p className="whitespace-pre-wrap break-words">{value.platform_override ?? value.platform_default}</p><p className="whitespace-pre-wrap break-words">{value.world_prompt || '尚未设置世界提示词'}</p></div>
      : <p className="mt-2">{error || '正在读取…'}</p>}
  </details>
}
