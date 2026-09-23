import { Suspense, useState } from 'react'
import { getAuthEpoch } from '../api/client'
import { worldTypes, type ConfigurationField } from '../api/worldTypes'
import { useAppStore } from '../store/app'
import { usePromptEditor } from './prompts/usePromptEditor'
import { worldTypePage } from '../world-types/registry'

const field = 'mt-2 w-full rounded-xl border border-slate-700 bg-panel p-2.5 text-sm outline-none focus:border-indigo-400'
const button = 'rounded-lg border border-slate-700 px-3 py-2 text-xs disabled:opacity-40'
const statuses: Record<string, string> = { ready: '初始化完成', pending: '等待初始化', needs_owner: '等待 Owner', needs_configuration: '需要补充配置', failed: '初始化失败' }

/** 当前存档的类型配置；展示未知页面、冲突和初始化失败，不把未完成初始化当成成功。 */
export function WorldTypeSettings({ onOpenConversation }: { onOpenConversation: (id: number) => void }) {
  const { user, worldName } = useAppStore()
  if (!user?.is_owner) return null
  const scope = `${getAuthEpoch()}:${worldName}:${user.id}:world-type`
  return <Editor key={scope} scope={scope} onOpenConversation={onOpenConversation} />
}

function Editor({ scope, onOpenConversation }: { scope: string; onOpenConversation: (id: number) => void }) {
  const app = useAppStore(), [error, setError] = useState(''), [initializing, setInitializing] = useState(false)
  const editor = usePromptEditor(scope, { read: worldTypes.current, write: worldTypes.save,
    values: value => value.configuration, onSaved: () => { void app.loadWorkspace() } })
  if (!editor.editing) return <p role="status">{editor.error || '正在读取世界类型…'}<button className={button} onClick={() => void editor.refresh()}>刷新世界类型</button></p>
  const { latest, draft } = editor.editing
  const Page = worldTypePage(latest.descriptor.frontend_entry)
  const fields = Object.entries(latest.descriptor.configuration_schema.properties ?? {})
  const unsupported = fields.some(([, f]) => !['string', 'integer', 'number', 'boolean'].includes(kind(f) ?? ''))
  async function initialize() {
    if (!editor.editing || initializing) return
    const epoch = getAuthEpoch(); setInitializing(true); setError('')
    try {
      await worldTypes.initialize(editor.editing.latest.revision)
      if (epoch === getAuthEpoch()) { await editor.refresh(); await app.loadWorkspace() }
    } catch { if (epoch === getAuthEpoch()) setError('初始化未能确认，请刷新状态后核对。') }
    finally { if (epoch === getAuthEpoch()) setInitializing(false) }
  }
  return <section aria-label="当前世界类型" className="space-y-4 rounded-2xl border border-slate-800 bg-panel p-4 text-sm">
    <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-semibold">{latest.descriptor.name}</h3><span className="text-xs text-slate-500">v{latest.type_version} · {statuses[latest.initialization.status] ?? latest.initialization.status}</span></div>
    {Page ? <Suspense fallback={<p role="status">正在加载类型页面…</p>}><Page view={latest} onOpenConversation={onOpenConversation} /></Suspense>
      : <p role="alert" className="text-amber-700">当前世界类型的专属页面未安装，请安装匹配的前端模块。</p>}
    {!!fields.length && <form onSubmit={event => { event.preventDefault(); void editor.save() }} className="space-y-3">
      <fieldset disabled={editor.busy || initializing} className="space-y-3">
        {fields.map(([key, schema]) => {
          const label = schema.title ?? key, type = kind(schema)
          const set = (value: unknown) => editor.change({ ...draft, [key]: value })
          return <label key={key} className="block text-xs">{label}{schema.widget === 'model_config'
            ? <select aria-label={label} className={field} value={String(draft[key] ?? '')} onChange={event => set(event.target.value ? Number(event.target.value) : null)}>
              <option value="">请选择本世界模型配置</option>{app.modelConfigs.map(config => <option key={config.id} value={config.id}>{config.name}</option>)}</select>
            : type === 'boolean' ? <input type="checkbox" className="ml-2" checked={draft[key] === true} onChange={event => set(event.target.checked)} />
            : schema.enum ? <select aria-label={label} className={field} value={String(draft[key] ?? '')} onChange={event => set(type === 'string' ? event.target.value : Number(event.target.value))}>
              {schema.enum.map(value => <option key={String(value)} value={String(value)}>{String(value)}</option>)}</select>
            : ['string', 'number', 'integer'].includes(type ?? '') ? <input aria-label={label} className={field} type={type === 'string' ? 'text' : 'number'}
              step={type === 'integer' ? 1 : undefined} min={schema.minimum} max={schema.maximum} maxLength={schema.maxLength}
              value={String(draft[key] ?? '')} onChange={event => set(type === 'string' ? event.target.value : event.target.value === '' ? null : Number(event.target.value))} />
            : <span className="mt-2 block text-amber-700">该字段需要类型专属编辑器。</span>}
            {schema.description && <span className="mt-1 block text-slate-500">{schema.description}</span>}</label>
        })}
      </fieldset>
      {editor.conflict && <p role="alert">配置已有新版本，当前输入已保留。<button type="button" className={`${button} ml-2`} onClick={editor.adopt}>采用服务端类型配置</button></p>}
      <button type="submit" className="rounded-lg bg-indigo-600 px-3 py-2 text-xs text-white disabled:opacity-40" disabled={editor.busy || !editor.dirty || editor.conflict || unsupported || initializing}>保存类型配置</button>
      <span role="status" className="ml-3 text-xs text-slate-500">{editor.busy ? '正在保存…' : editor.dirty ? '有未保存修改' : '类型配置已同步'}</span>
    </form>}
    {!!latest.initialization.required_fields.length && <p className="text-xs text-amber-700">待补充：{latest.initialization.required_fields.map(key => latest.descriptor.configuration_schema.properties?.[key]?.title ?? key).join('、')}</p>}
    {latest.initialization.error_code && <p role="alert" className="text-xs text-red-500">类型初始化未完成，已保存的配置保留，可修正后重试。</p>}
    {(error || editor.error) && <p role="alert" className="text-xs text-red-500">{error || editor.error}</p>}
    <div className="flex gap-2"><button className={button} disabled={initializing || editor.busy} onClick={() => void editor.refresh()}>刷新类型状态</button>
      {latest.initialization.status !== 'ready' && <button className={button} disabled={initializing || editor.busy || editor.dirty} onClick={() => void initialize()}>重试初始化</button>}</div>
  </section>
}

function kind(schema: ConfigurationField) { return schema.type ?? schema.anyOf?.find(item => item.type !== 'null')?.type }
