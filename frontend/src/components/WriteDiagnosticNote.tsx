import type { WriteDiagnostic } from '../api/client'

/** Owner 私有拒绝诊断，说明本项事实，不授予停止权限或重试其他已成功项。
 * @param value 服务端已授权、已限长的固定原因与建议。
 */
export function WriteDiagnosticNote({ value }: { value: WriteDiagnostic }) {
  if (value.version !== 1) return <p className="mt-2 text-amber-300">当前版本无法展示此拒绝诊断。</p>
  return <section role="note" aria-label="写入受阻原因" className="mt-3 space-y-2 rounded-lg border border-amber-800/50 bg-amber-950/20 p-3 text-amber-200">
    <p>{value.message}</p>
    {value.other_sessions_blocking && <p>同一工作区还有非本会话可管理的服务。请 Owner 协调其他会话或历史回收记录，不得擅自停止。</p>}
    {!!value.services?.length && <div>
      <p className="text-xs text-slate-400">本会话相关服务（拒绝时状态）</p>
      <ul className="mt-1 space-y-1 font-mono text-xs text-slate-300">
        {value.services.map((service) => <li key={service.runtime_id} className="break-all">{service.runtime_id} · {service.state}</li>)}
      </ul>
    </div>}
    {value.services_truncated && <p>这里只列出部分本会话实例，不能据此认为已包含全部阻塞。</p>}
    <ul className="list-disc space-y-1 pl-4">{value.next_steps.map((step, index) => <li key={index}>{step}</li>)}</ul>
  </section>
}
