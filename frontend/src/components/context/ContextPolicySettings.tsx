import { useEffect, useRef } from 'react'
import { getAuthEpoch, type Role } from '../../api/client'
import { contextPolicy, type ContextPolicy } from '../../api/contextPolicy'
import { useAppStore } from '../../store/app'
import { usePromptEditor } from '../prompts/usePromptEditor'

const field = 'mt-2 block w-full rounded-xl border border-slate-700 bg-panel p-2.5 text-xs outline-none focus:border-indigo-400'
const button = 'rounded-lg border border-slate-700 px-3 py-2 text-xs disabled:opacity-40'
const notices: Record<string, string> = {
  reserve_adjusted: '当前窗口较小，建议预留已调整为窗口的一半。',
  threshold_clamped: '成员或模型窗口已变小，实际阈值已收紧；原设置保留，请核对。',
  target_clamped: '目标已随实际阈值收紧。', no_available_model: '会话尚无有效模型，自动压缩暂不可用。',
  model_unavailable: '指定的摘要模型已不在有效成员中，自动压缩暂停，请重新选择。',
}

/** 世界默认与会话覆盖共用表单；草稿隔离、版本冲突保留，读取不会触发压缩。 */
export function ContextPolicySettings({ conversationId, roles }: { conversationId?: number; roles: Role[] }) {
  const { user, worldName } = useAppStore()
  if (!user?.is_owner) return null
  const scope = `${getAuthEpoch()}:${worldName}:${user.id}:context-policy:${conversationId ?? 'world'}`
  return <Editor key={scope} scope={scope} conversationId={conversationId} roles={roles} />
}

function Editor({ scope, conversationId, roles }: { scope: string; conversationId?: number; roles: Role[] }) {
  const world = conversationId === undefined
  const editor = usePromptEditor(scope, {
    read: signal => contextPolicy.read(conversationId, signal),
    write: (value: ContextPolicy | null, revision) => contextPolicy.save(conversationId, value, revision),
    values: record => record.inherited ? null : record.policy,
  })
  const refresh = useRef(editor.refresh); refresh.current = editor.refresh
  useEffect(() => {
    const update = () => void refresh.current()
    const timer = window.setInterval(update, 5000)
    window.addEventListener('focus', update)
    return () => { window.clearInterval(timer); window.removeEventListener('focus', update) }
  }, [])
  if (!editor.editing) return <p role="status">{editor.error || '正在读取压缩策略…'}<button className={button} onClick={() => void editor.refresh()}>刷新设置</button></p>
  const { latest, draft } = editor.editing
  const value = draft ?? latest.policy, disabled = !world && draft === null, limits = latest.limits
  const change = (patch: Partial<ContextPolicy>) => editor.change({ ...value, ...patch })
  const cap = limits?.ceiling_tokens
  const reserve = cap === undefined ? value.reserve_tokens : value.reserve_tokens < cap ? value.reserve_tokens : Math.floor(cap / 2)
  const recommended = cap === undefined ? undefined : cap - reserve
  const aboveCap = cap !== undefined && value.trigger_tokens !== null && value.trigger_tokens > cap
  const modelRoles = world ? roles : roles.filter(role => limits?.roles.some(row => row.role_id === role.id))
  return <section aria-label={world ? '世界自动压缩设置' : '会话自动压缩设置'} className="space-y-4 text-xs">
    <div><h4 className="font-semibold">{world ? '世界默认压缩策略' : '自动压缩设置'}</h4>
      <p className="mt-2 leading-relaxed text-slate-500">{world ? '供这个世界的会话继承，阈值按各会话的角色窗口计算。' : '作用于当前会话的共享历史。自动压缩与原任务共用决策预算。'}</p></div>
    {limits && <div className="space-y-2 rounded-xl border border-indigo-200 bg-panel p-3" aria-label="会话阈值建议">
      <p>会话阈值上限 <strong>{limits.ceiling_tokens.toLocaleString()}</strong> Token</p>
      <p>推荐触发阈值 = {limits.ceiling_tokens.toLocaleString()} − {reserve.toLocaleString()} = <strong>{recommended?.toLocaleString()}</strong> Token</p>
      <p className="leading-relaxed text-slate-500">建议预留 50,000 Token 给提示词、工具和输出；预留可调整，实际调用仍会检查完整输入。</p>
      <details><summary className="cursor-pointer">各角色窗口</summary><ul className="mt-2 space-y-1">{limits.roles.map(row => <li key={row.role_id}>{row.name} · {row.window_tokens.toLocaleString()} Token</li>)}</ul></details>
      <p className="text-slate-500">当前生效阈值 {latest.effective?.trigger_tokens?.toLocaleString()} · {latest.inherited ? `继承世界版本 ${latest.world_revision}` : `会话版本 ${latest.revision}`}</p>
      {latest.notices?.map(code => <p key={code} className="text-amber-700">{notices[code] ?? code}</p>)}
    </div>}
    <form className="space-y-3" onSubmit={event => { event.preventDefault(); void editor.save() }}>
      {!world && <label className="flex items-center gap-2"><input type="checkbox" checked={draft !== null} disabled={editor.busy}
        onChange={event => editor.change(event.target.checked ? { ...latest.policy } : null)} />为当前会话单独设置</label>}
      <fieldset disabled={editor.busy || disabled} className="space-y-3">
        <label className="flex items-center gap-2"><input type="checkbox" checked={value.enabled} onChange={event => change({ enabled: event.target.checked })} />启用自动压缩</label>
        <label className="block">触发阈值<input aria-label="自动压缩触发阈值" type="number" min={256} max={cap || 10000000} className={field}
          value={value.trigger_tokens ?? ''} placeholder={recommended?.toLocaleString() ?? '留空：按会话推荐值动态计算'}
          onChange={event => change({ trigger_tokens: event.target.value === '' ? null : Number(event.target.value) })} /></label>
        <p className="text-slate-500">留空会自动跟随推荐值。填写后可高于推荐值，但不能超过会话上限。</p>
        <button type="button" className={button} disabled={value.trigger_tokens === null} onClick={() => change({ trigger_tokens: null })}>跟随推荐阈值</button>
        <label className="block">建议预留<input aria-label="自动压缩建议预留" type="number" min={0} max={10000000} className={field}
          value={value.reserve_tokens} onChange={event => change({ reserve_tokens: Number(event.target.value) })} /></label>
        <label className="block">压缩后期望材料规模<input aria-label="自动压缩目标规模" type="number" min={128} max={value.trigger_tokens ? value.trigger_tokens - 1 : undefined}
          value={value.target_tokens ?? ''} placeholder="留空：触发阈值的一半" className={field}
          onChange={event => change({ target_tokens: event.target.value === '' ? null : Number(event.target.value) })} /></label>
        <label className="block">自动摘要模型<select aria-label="自动摘要模型" className={field} value={value.model_role_id ?? ''}
          onChange={event => change({ model_role_id: event.target.value ? Number(event.target.value) : null })}>
          <option value="">跟随当前执行所用的角色模型</option>
          {value.model_role_id && !modelRoles.some(role => role.id === value.model_role_id) && <option value={value.model_role_id}>原模型不可用，请重选</option>}
          {modelRoles.map(role => <option key={role.id} value={role.id}>{role.model_name} · {role.name}</option>)}
        </select></label>
        <label className="block">自动压缩提示词<textarea aria-label="自动压缩提示词" rows={4} maxLength={10000} className={field}
          value={value.instructions} onChange={event => change({ instructions: event.target.value })} /></label>
        <p className="text-slate-500">用于之后的自动压缩。主动压缩的“本次保留重点”独立保存。</p>
        <details><summary className="cursor-pointer text-slate-500">保留与频率</summary><div className="mt-3 space-y-3">
          {([{ key: 'keep_recent', label: '自动压缩最近保留条数', min: 0, max: 200 },
            { key: 'summary_tokens', label: '自动摘要长度上限', min: 128, max: 100000 },
            { key: 'cooldown_seconds', label: '自动压缩冷却秒数', min: 0, max: 86400 },
            { key: 'min_new_tokens', label: '再次压缩所需新增 Token', min: 0, max: 1000000 }] as const).map(item =>
            <label key={item.key} className="block">{item.label}<input aria-label={item.label} type="number" min={item.min} max={item.max} className={field}
              value={value[item.key]} onChange={event => change({ [item.key]: Number(event.target.value) })} /></label>)}
          <p className="leading-relaxed text-slate-500">最近消息、置顶内容和执行事实优先保留。无法达到目标时显示实际结果，不丢弃这些材料。</p>
        </div></details>
      </fieldset>
      {aboveCap && !disabled && <p role="alert" className="text-red-500">触发阈值不能高于当前会话上限 {cap?.toLocaleString()}。</p>}
      {editor.error && <p role="alert" className="text-red-500">{editor.error}</p>}
      {editor.conflict && <div className="space-y-2 rounded-xl border border-amber-400 p-3"><p>服务端配置已更新，当前输入保留。核对后可采用新配置。</p>
        <button type="button" className={button} onClick={editor.adopt}>采用服务端压缩设置</button></div>}
      <div className="flex flex-wrap gap-2"><button type="submit" className="rounded-lg bg-indigo-600 px-3 py-2 text-white disabled:opacity-40"
        disabled={editor.busy || !editor.dirty || editor.conflict || (aboveCap && !disabled)}>保存压缩设置</button>
        <button type="button" className={button} disabled={editor.busy} onClick={() => void editor.refresh()}>刷新压缩设置</button></div>
      <p role="status" className="text-slate-500">{editor.busy ? '正在保存…' : editor.dirty ? '有未保存修改' : '压缩设置已同步'}</p>
    </form>
  </section>
}
