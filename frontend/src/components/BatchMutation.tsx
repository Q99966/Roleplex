import { useState } from 'react'
import { EditErrorNote } from './EditErrorNote'
import { WriteWaitNote } from './WriteWaitNote'
import type { BatchMutationDetails } from '../api/client'
import { WriteDiff } from './WriteDiff'
import { WriteDiagnosticNote } from './WriteDiagnosticNote'

const STATUS: Record<string, string> = { success: '已应用', failed: '失败', not_executed: '未执行',
  running: '执行中', result_unconfirmed: '结果未确认' }

/** 控制字符仅可见显示，不执行路径中的终端或双向控制序列。
 * @param text Owner 私有路径。
 */
function visible(text: string): string {
  return text.replace(/[\u0000-\u0008\u000b-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/g,
    (char) => `\\u${char.charCodeAt(0).toString(16).padStart(4, '0')}`)
}

/** 逐项展示提交事实，不把部分失败简化为整批失败或无修改。
 * @param item 当前真实父调用内的文件节点。
 * @param single 单项直接沿用原 diff，不额外增加折叠入口。
 */
function MutationNode({ item, single }: { item: BatchMutationDetails['items'][number]; single: boolean }) {
  const file = item.write?.files[0]
  const label = file?.operation === 'unchanged' ? '内容无变化' : STATUS[item.status] ?? '状态未知'
  const title = <>{visible(item.path)} · {label}
    {file && file.added !== null && file.removed !== null && <span className="ml-2 font-mono">
      <span className="text-emerald-300">+{file.added}</span> <span className="text-red-300">-{file.removed}</span>
    </span>}
    {item.error_code && <span className="ml-2 text-red-300">{item.error_code}</span>}
  </>
  const body = <>
    {!!item.created_parent_count && <p className={item.applied ? "text-slate-400" : "text-amber-300"}>本次创建了 {item.created_parent_count} 个父目录{item.applied ? "。" : "，可能留下空目录。"}</p>}
    {item.edit_error && <EditErrorNote value={item.edit_error} />}
    {item.diagnostic && <WriteDiagnosticNote value={item.diagnostic} />}
    {item.applied === null && <p className="text-amber-300">文件可能已修改，结果未确认；请先核查，不要直接重试。</p>}
    {item.applied === false && <p className="text-slate-400">本项未写入。</p>}
    {item.write ? <WriteDiff value={item.write} showFileHeading={single} />
      : item.applied && <p className="text-amber-300">文件修改已确认，本次未记录差异。</p>}
  </>
  if (single) return <div>{!file && <h5 className="break-all py-2 text-slate-200">{title}</h5>}{body}</div>
  return <details open className="min-w-0 overflow-hidden rounded-lg border border-slate-700">
    <summary className="cursor-pointer break-all px-3 py-2 text-slate-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-400">{title}</summary>
    <div className="border-t border-slate-800 px-3 pb-3 pt-2">{body}</div>
  </details>
}

/** 一个批次的多文件 diff，沿用原计算结果、权限和视觉样式。
 * @param value 经 Owner 详情接口授权的完整修改批次，分段渲染不丢节点。
 */
export function BatchMutation({ value }: { value: BatchMutationDetails }) {
  const [visibleCount, setVisibleCount] = useState(50)
  if (value.version !== 1) return <p>当前版本不支持此批量修改格式。</p>
  const notice: Record<string, string> = {
    partial: '部分修改完成，后续项已停止；已应用内容不会自动回滚。',
    rejected: '批次未通过写前检查，未开始修改。', cancelled: '批次已取消，已确认的修改不会回滚。',
    result_unconfirmed: '本批存在未确认的写入结果，先核查文件再决定后续操作。',
  }
  return <section aria-label="本次批量修改" className="mt-3 space-y-2">
    {value.wait_diagnostic && <WriteWaitNote value={value.wait_diagnostic} />}
    {notice[value.status] && <p className="text-amber-300">{notice[value.status]}</p>}
    {value.items.length > 50 && <p className="text-slate-500">共 {value.items.length} 项 · 已显示 {Math.min(visibleCount, value.items.length)} 项</p>}
    {value.items.slice(0, visibleCount).map((item) => <MutationNode key={item.id} item={item} single={value.items.length === 1} />)}
    {visibleCount < value.items.length && <button type="button" className="rounded-lg border border-slate-700 px-3 py-2 text-indigo-500" onClick={() => setVisibleCount(count => count + 50)}>显示后续文件</button>}
  </section>
}
