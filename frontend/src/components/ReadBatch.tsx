import type { ReadBatchDetails } from '../api/client'

const STATUS: Record<string, string> = { pending: '未开始', running: '读取中', success: '已读取',
  failed: '失败', rejected: '已拒绝', cancelled: '已取消', not_executed: '未执行', budget_exhausted: '本批预算未覆盖，未读取' }

/** 私有文件名/正文中的控制字符可见转义，保持纯文本，不执行 HTML。
 * @param text Owner 详情返回的原文。
 */
function visible(text: string): string {
  return text.replace(/[\u0000-\u0008\u000b-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/g,
    (char) => `\\u${char.charCodeAt(0).toString(16).padStart(4, '0')}`)
}

/** 一个真实批量调用下的文件节点；不是多个调用的探索归组，也不是统一文件快照。
 * @param value Owner 主动展开后经既有详情接口取得的有界逐项结果。
 */
export function ReadBatch({ value }: { value: ReadBatchDetails }) {
  if (value.version !== 1) return <p>当前版本不支持此批量读取格式。</p>
  return <section aria-label="本次批量读取" className="mt-3 space-y-2">
    {value.status === 'partial' && <p className="text-amber-300">部分读取完成，失败项不影响下列已读取结果。</p>}
    {value.status === 'cancelled' && <p className="text-amber-300">批量读取已取消，仅保留已观察到的逐项结果。</p>}
    {value.items.map((item) => <details key={item.id} className="min-w-0 overflow-hidden rounded-lg border border-slate-700">
      <summary className="cursor-pointer break-all px-3 py-2 text-slate-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-400">
        {visible(item.path)}{item.result && 'next_line' in item.result ? ` · ${item.result.start_line}～${item.result.end_line ?? '无完整行'} 行` : ''} · {STATUS[item.status] ?? '状态未知'}
        {item.error_code && <span className="ml-2 text-red-300">{item.error_code}</span>}
      </summary>
      <div className="space-y-2 border-t border-slate-800 p-3">
        {item.result ? <>
          <p>{item.result.bytes} 字节 · {item.result.eof ? '已到文件末尾' : 'next_line' in item.result ? `续读行号 ${item.result.next_line}` : `续读偏移 ${item.result.next_offset}`}</p>
          {item.output_limited && <p className="text-amber-300">内容受本批预算限制；hash 与续读位置保留。</p>}
          {'next_line' in item.result && <p>实际行范围：{item.result.start_line}～{item.result.end_line ?? '无完整行'} · 扫描 {item.result.scanned_bytes} 字节</p>}
          {'limited_reason' in item.result && item.result.limited_reason === 'line_too_long' && <p>该行超过本次额度，请按字节分段读取。</p>}
          <p className="break-all font-mono">全文件 SHA-256：{item.result.sha256}</p>
          <pre role="region" aria-label={`读取内容：${visible(item.path)}`}
            className="max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-slate-950 p-2 font-mono text-xs">{visible(item.result.text) || '（空内容）'}</pre>
        </> : <p>本项没有已记录的读取结果。</p>}
      </div>
    </details>)}
  </section>
}
