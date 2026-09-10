import { useState, type FormEvent } from 'react'
import { Check, FileText, FolderInput, FolderKanban, RefreshCw, ShieldCheck, Trash2 } from 'lucide-react'
import { useAppStore } from '../store/app'
import type { WorkspaceAvailability } from '../api/client'

const STATUS_LABELS: Record<WorkspaceAvailability, string> = {
  available: '可用',
  unavailable: '目录不可用',
  busy: '执行占用中',
  disabled: '已停用',
}

const STATUS_STYLES: Record<WorkspaceAvailability, string> = {
  available: 'border-emerald-800/60 bg-emerald-950/60 text-emerald-300',
  unavailable: 'border-red-900/60 bg-red-950/50 text-red-300',
  busy: 'border-amber-800/60 bg-amber-950/50 text-amber-300',
  disabled: 'border-slate-700 bg-slate-900 text-slate-400',
}

/** 当前 World 工作区设置；规范绝对根只向当前 World Owner 显示和提交。 */
export function WorkspaceSettingsPanel() {
  const {
    worldName,
    workspaceBindings,
    workspaceCapabilities,
    createWorkspaceBinding,
    updateWorkspaceBinding,
    validateWorkspaceBinding,
    deleteWorkspaceBinding,
  } = useAppStore()
  const [mode, setMode] = useState<'existing' | 'create'>('existing')
  const [form, setForm] = useState({ displayName: '', rootPath: '', acknowledged: false })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  /** @param event 添加工作区表单提交事件。 */
  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await createWorkspaceBinding({
        display_name: form.displayName,
        root_path: form.rootPath.trim(),
        create_directory: mode === 'create',
        acknowledge_existing_content: mode === 'existing' && form.acknowledged,
      })
      setForm({ displayName: '', rootPath: '', acknowledged: false })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '添加工作区失败')
    } finally {
      setBusy(false)
    }
  }

  /** @param action 单条工作区异步操作。 */
  async function run(action: () => Promise<void>) {
    setBusy(true)
    setError(null)
    try {
      await action()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '工作区操作失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-5 text-xs">
      <section aria-labelledby="workspace-boundary-title" className="rounded-2xl border border-indigo-500/25 bg-indigo-950/20 p-4">
        <div className="flex items-center gap-2 text-indigo-300">
          <ShieldCheck size={16} />
          <h4 id="workspace-boundary-title" className="font-bold">本轮文件边界</h4>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2" aria-label="工作区边界轨迹">
          <span className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-slate-200">World · {worldName}</span>
          <span aria-hidden="true" className="text-slate-600">→</span>
          <span className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-slate-200">Workspace Binding</span>
          <span aria-hidden="true" className="text-slate-600">→</span>
          <span className="max-w-full break-all rounded-lg border border-indigo-500/40 bg-indigo-950/40 px-3 py-2 font-mono text-indigo-200">
            {form.rootPath || '等待输入绝对根目录'}
          </span>
        </div>
        <p className="mt-3 leading-relaxed text-slate-400">
          文件内容和命令输出可能发送给角色绑定的模型厂商。可开启原生文件读写和目录、读取、计数等固定命令。
        </p>
      </section>

      <section aria-labelledby="workspace-add-title" className="rounded-2xl border border-slate-800 bg-slate-950/40 p-4">
        <div className="flex items-center gap-2">
          <FolderInput size={15} className="text-indigo-400" />
          <h4 id="workspace-add-title" className="font-bold text-slate-200">添加工作区</h4>
        </div>
        <form onSubmit={submit} className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2">
            <label className="block">
              <span className="font-medium text-slate-400">显示名称</span>
              <input
                required
                value={form.displayName}
                onChange={(event) => setForm({ ...form, displayName: event.target.value })}
                placeholder="例如：Alpha 文件实验区"
                className="mt-1.5 w-full rounded-xl border border-slate-800 bg-slate-950 px-3 py-2.5 text-slate-200 outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500/30"
              />
            </label>
            <label className="block md:col-span-2">
              <span className="font-medium text-slate-400">工作区绝对路径</span>
              <input
                required
                value={form.rootPath}
                onChange={(event) => setForm({ ...form, rootPath: event.target.value })}
                placeholder={mode === 'existing' ? '/srv/projects/example' : '/srv/projects/new-workspace'}
                className="mt-1.5 w-full rounded-xl border border-slate-800 bg-slate-950 px-3 py-2.5 font-mono text-slate-200 outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500/30"
              />
              <span className="mt-1 block text-[10px] leading-relaxed text-slate-500">
                路径属于当前后端主机，只对本 World Owner 可见；Agent 只能访问该根目录以内。
              </span>
            </label>
            <fieldset className="md:col-span-2">
              <legend className="font-medium text-slate-400">目录操作</legend>
              <div className="mt-2 flex flex-wrap gap-2">
                {(['existing', 'create'] as const).map((value) => (
                  <button
                    key={value}
                    type="button"
                    aria-pressed={mode === value}
                    onClick={() => setMode(value)}
                    className={`rounded-lg border px-3 py-2 transition motion-reduce:transition-none ${
                      mode === value
                        ? 'border-indigo-500 bg-indigo-600 text-white'
                        : 'border-slate-800 bg-slate-900 text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    {value === 'existing' ? '登记既有目录' : '创建空目录'}
                  </button>
                ))}
              </div>
            </fieldset>
            {mode === 'existing' && (
              <label className="flex items-start gap-2 rounded-xl border border-amber-900/40 bg-amber-950/20 p-3 text-amber-200 md:col-span-2">
                <input
                  required
                  type="checkbox"
                  checked={form.acknowledged}
                  onChange={(event) => setForm({ ...form, acknowledged: event.target.checked })}
                  className="mt-0.5 accent-indigo-500"
                />
                <span>我确认该目录的文件内容可能被发送给角色绑定的外部模型厂商。</span>
              </label>
            )}
            <div className="flex justify-end md:col-span-2">
              <button
                type="submit"
                disabled={busy}
                className="inline-flex min-h-10 items-center gap-2 rounded-xl bg-indigo-600 px-4 py-2 font-semibold text-white hover:bg-indigo-500 disabled:opacity-50"
              >
                <Check size={14} />添加工作区
              </button>
            </div>
        </form>
      </section>

      {error && <div role="alert" className="rounded-xl border border-red-900/50 bg-red-950/30 p-3 text-red-300">{error}</div>}

      <section aria-labelledby="workspace-list-title">
        <div className="mb-2 flex items-center gap-2 text-slate-400">
          <FolderKanban size={15} />
          <h4 id="workspace-list-title" className="font-bold uppercase tracking-wider">当前 World 工作区 ({workspaceBindings.length})</h4>
        </div>
        <div className="space-y-3">
          {workspaceBindings.map((binding) => (
            <article key={binding.id} className="rounded-2xl border border-slate-800 bg-slate-950/55 p-4">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <h5 className="text-sm font-bold text-white">{binding.display_name}</h5>
                    <span className={`rounded-full border px-2 py-0.5 text-[10px] ${STATUS_STYLES[binding.availability]}`}>
                      {STATUS_LABELS[binding.availability]}
                    </span>
                  </div>
                  <p className="mt-1.5 break-all font-mono text-[11px] text-slate-400">
                    {binding.root_path}
                  </p>
                  <p className="mt-1 text-[10px] text-slate-500">
                    managed_directory · 已绑定 {binding.bound_conversation_count} 个会话
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void run(() => validateWorkspaceBinding(binding.id))}
                    className="inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-slate-300 hover:bg-slate-800 disabled:opacity-50"
                  >
                    <RefreshCw size={12} />复核
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void run(() => updateWorkspaceBinding(binding.id, { active: !binding.active }))}
                    className="min-h-9 rounded-lg border border-slate-700 px-3 py-1.5 text-slate-300 hover:bg-slate-800 disabled:opacity-50"
                  >
                    {binding.active ? '停用' : '启用'}
                  </button>
                  <button
                    type="button"
                    disabled={busy || binding.availability === 'busy'}
                    onClick={() => {
                      if (!window.confirm('解除登记只删除 Roleplex 中的绑定，不会删除物理目录。继续吗？')) return
                      void run(() => deleteWorkspaceBinding(binding.id))
                    }}
                    aria-label={`解除登记 ${binding.display_name}`}
                    className="inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-red-900/50 px-3 py-1.5 text-red-400 hover:bg-red-950/50 disabled:opacity-50"
                  >
                    <Trash2 size={12} />解除登记
                  </button>
                </div>
              </div>
              <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-3">
                <button
                  type="button"
                  aria-pressed={binding.file_tools_enabled}
                  disabled={busy || !binding.active}
                  onClick={() => void run(() => updateWorkspaceBinding(binding.id, { file_tools_enabled: !binding.file_tools_enabled }))}
                  className={`flex min-h-11 items-center gap-2 rounded-xl border px-3 py-2 text-left ${
                    binding.file_tools_enabled
                      ? 'border-indigo-500/50 bg-indigo-950/40 text-indigo-200'
                      : 'border-slate-800 bg-slate-900/60 text-slate-500'
                  }`}
                >
                  <FileText size={14} />原生文件读写 · {binding.file_tools_enabled ? '已开启' : '关闭'}
                </button>
                <button
                  type="button"
                  aria-pressed={binding.basic_commands_enabled}
                  disabled={busy || !binding.active}
                  onClick={() => void run(() => updateWorkspaceBinding(binding.id, { basic_commands_enabled: !binding.basic_commands_enabled }))}
                  className={`flex min-h-11 items-center rounded-xl border px-3 py-2 text-left ${binding.basic_commands_enabled
                    ? 'border-indigo-500/50 bg-indigo-950/40 text-indigo-200'
                    : 'border-slate-800 bg-slate-900/60 text-slate-500'}`}
                >
                  结构化命令 · {binding.basic_commands_enabled ? '已开启' : '关闭'}
                </button>
                <button type="button" aria-pressed={binding.shell_enabled} disabled={busy || !binding.active || !workspaceCapabilities?.shell_available}
                  onClick={() => void run(() => updateWorkspaceBinding(binding.id, { shell_enabled: !binding.shell_enabled }))}
                  className={`flex min-h-11 items-center rounded-xl border px-3 py-2 text-left ${binding.shell_enabled
                    ? 'border-amber-500/50 bg-amber-950/40 text-amber-200' : 'border-slate-800 bg-slate-900/60 text-slate-500'}`}>
                  审批 Shell · {!workspaceCapabilities?.shell_available ? '本机不可用' : binding.shell_enabled ? '已开启' : '关闭'}
                </button>
              </div>
            </article>
          ))}
          {workspaceBindings.length === 0 && (
            <div className="rounded-2xl border border-dashed border-slate-800 bg-slate-950/20 py-8 text-center text-slate-600">
              当前 World 尚未登记工作区。
            </div>
          )}
        </div>
      </section>
    </div>
  )
}
