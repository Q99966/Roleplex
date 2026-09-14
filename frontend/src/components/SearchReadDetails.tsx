import type { LineReadResult, SearchResult } from '../api/client'
import { visibleScript } from './ShellApprovals'

/** 展示一次实际范围读取，不以当前文件重建历史。
 * @param value Owner 详情返回的有界行结果。
 */
export function RangeReadDetails({ value }: { value: LineReadResult }) {
  return <section aria-label="本次按行读取" className="mt-3 space-y-2">
    <p>实际行范围：{value.start_line}～{value.end_line ?? '无完整行'} · 返回 {value.bytes} 字节</p>
    <p>{value.eof ? '已到文件末尾' : `续读行号 ${value.next_line}`} · 扫描 {value.scanned_bytes} 字节</p>
    {value.limited_reason && <p className="text-amber-300">{value.limited_reason === 'line_too_long'
      ? `该行超过本次额度，请从字节位置 ${value.start_offset ?? '读取结果中的起点'} 分段读取。` : '本次预算仅覆盖部分内容，可缩小范围或按字节读取。'}</p>}
    <p className="break-all font-mono">全文件 SHA-256：{value.sha256}</p>
    <pre role="region" aria-label="行范围内容" className="max-h-64 overflow-auto whitespace-pre-wrap break-all rounded bg-slate-950 p-2 font-mono text-xs">{visibleScript(value.text) || '（空内容）'}</pre>
  </section>
}

/** 按文件归组真实命中，查询仅取 Owner 加密详情，不重建历史内容。
 * @param value 已验证版本与预算的搜索结果。
 * @param input Owner 私有输入；兼容旧记录缺失或无法解析的情况。
 */
export function SearchDetails({ value, input }: { value: SearchResult; input?: { text: string } | null }) {
  let parameters: { query?: string; queries?: string[]; match?: string; path?: string; mode?: string } = {}
  try { parameters = input ? JSON.parse(input.text) : {} } catch { /* 旧详情不可解析时仍展示执行结果。 */ }
  const terms = parameters.queries ?? (parameters.query ? [parameters.query] : [])
  const groups = new Map<string, SearchResult['matches']>()
  for (const hit of value.matches) groups.set(hit.path, [...(groups.get(hit.path) ?? []), hit])
  if (value.version !== 1) return <p>当前版本不支持此搜索格式。</p>
  return <section aria-label="本次文件搜索" className="mt-3 space-y-2">
    {terms.length > 0 && <p className="break-all">查询：{terms.map((term) => visibleScript(term)).join('、')} · {parameters.match === 'all' ? '同一行包含全部词（AND）' : '匹配任意词（OR）'}</p>}
    {input != null && <p className="break-all">搜索范围：{visibleScript(parameters.path ?? '.')}</p>}
    <p>{value.status === 'complete' ? '指定范围搜索完成' : '搜索未覆盖全部范围，请缩小路径或查询'} · 命中 {groups.size} 个文件 / {value.matches.length} 项</p>
    <p className="text-xs">扫描 {value.scanned_files} 个文件 / {value.scanned_bytes} 字节{value.visited_entries > 0 ? ` · 遍历 ${value.visited_entries} 个目录项` : ''}</p>
    {!value.matches.length && <p>{value.status === 'complete' ? '指定范围没有匹配。' : '已覆盖范围没有匹配，不代表整个工作区无结果。'}</p>}
    {[...groups].map(([path, hits]) => <details key={path} className="min-w-0 rounded border border-slate-700">
      <summary className="cursor-pointer break-all p-2">{visibleScript(path)} · {hits[0].line_number === null ? '文件名匹配' : `${hits.length} 处匹配`}</summary>
      <div className="space-y-2 p-2">
        {hits.map((hit, index) => hit.text !== null && <div key={index}>
          <p className="break-all text-xs">第 {hit.line_number} 行{hit.matched_queries?.length && terms.length > 1 ? ` · 命中：${hit.matched_queries.map((i) => visibleScript(terms[i] ?? '')).join('、')}` : ''}</p>
          <pre role="region" aria-label={`搜索匹配：${visibleScript(hit.path)}`} className="max-h-64 overflow-auto whitespace-pre-wrap break-all bg-slate-950 p-2 text-xs">
            {[...hit.context_before, { line_number: hit.line_number!, text: hit.text, truncated: hit.truncated }, ...hit.context_after]
              .map((line) => `${line.line_number}: ${visibleScript(line.text)}${line.truncated ? '（片段）' : ''}`).join('\n')}
          </pre>
        </div>)}
        <p className="break-all text-xs">{hits[0].version_confirmed ? `全文件 SHA-256：${hits[0].sha256}` : '文件名结果未校验内容版本，请读取后再修改。'}</p>
      </div>
    </details>)}
    {value.issues.map((issue, index) => <p key={index} className="break-all text-xs text-amber-300">未覆盖原因：{issue.reason}{issue.path ? ` · ${visibleScript(issue.path)}` : ''}</p>)}
  </section>
}
