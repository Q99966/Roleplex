import { useMemo } from 'react'
import { Diff, Hunk, type HunkData } from 'react-diff-view'
import type { FileChange, WriteDetails } from '../api/client'
import 'react-diff-view/style/index.css'
import './write-diff.css'

const REASONS: Record<string, string> = {
  input_budget: '修改成功，差异超出 256 KiB 计算输入预算。', line_budget: '修改成功，差异超出计算行数预算。',
  queue_full: '修改成功，差异计算容量已满，本次未采集。', queue_timeout: '修改成功，差异等待超时。',
  compute_timeout: '修改成功，差异计算达到截止时间。', cancelled: '差异计算已取消，已确认的文件写入不会回滚。',
  capture_failed: '差异采集不可用，不代表文件未修改。', not_text: '旧版本不是可比较的 UTF-8 文本，未生成差异。',
  shutdown: '应用关闭期间未完成差异采集，文件不会自动回滚。',
}

/** 控制字符按可见转义显示，保留事实但不执行终端或双向文本控制。
 * @param text 文件路径或一行原始文本。
 */
function visible(text: string): string {
  return text.replace(/[\u0000-\u0008\u000b-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/g,
    (char) => `\\u${char.charCodeAt(0).toString(16).padStart(4, '0')}`)
}

/** 项目差异行适配为只读渲染器输入，不使用 patch 解析器推断事实。
 * @param file Owner 接口返回的有界文件节点。
 * @param showHeading 批量父节点已有文件标题时不重复显示。
 */
function FileNode({ file, showHeading }: { file: FileChange; showHeading: boolean }) {
  const hunks = useMemo<HunkData[]>(() => file.hunks.map((hunk) => ({
    content: `@@ -${hunk.old_start},${hunk.old_lines} +${hunk.new_start},${hunk.new_lines} @@`,
    oldStart: hunk.old_start, oldLines: hunk.old_lines, newStart: hunk.new_start, newLines: hunk.new_lines,
    changes: hunk.lines.map((line) => {
      const content = `${line.kind === 'insert' ? '+ ' : line.kind === 'delete' ? '- ' : '  '}${visible(line.text)}${line.ending === 'none' ? ' ⟪无末尾换行⟫' : line.ending === 'crlf' ? ' ⟪CRLF⟫' : ''}`
      if (line.kind === 'context') return { type: 'normal' as const, isNormal: true as const, oldLineNumber: line.old_line!, newLineNumber: line.new_line!, content }
      if (line.kind === 'insert') return { type: 'insert' as const, isInsert: true as const, lineNumber: line.new_line!, content }
      return { type: 'delete' as const, isDelete: true as const, lineNumber: line.old_line!, content }
    }),
  })), [file])
  const operation = { created: '新建', modified: '修改', unchanged: '内容无变化' }[file.operation] ?? '文件变更'
  return <section className="mt-2 min-w-0 overflow-hidden border-t border-slate-700">
    {showHeading && <h5 className="break-all py-2 text-slate-200">
      {visible(file.path)} · {operation}
      {file.added !== null && file.removed !== null && <span className="ml-2 font-mono"><span className="text-emerald-300">+{file.added}</span> <span className="text-red-300">-{file.removed}</span></span>}
    </h5>}
    <div role="region" aria-label={`文件差异：${visible(file.path)}`} className="roleplex-write-diff max-h-80 overflow-auto border-t border-slate-800">
      {hunks.length ? <Diff viewType="unified" diffType={file.operation === 'created' ? 'add' : 'modify'} hunks={hunks}>
        {(items) => items.map((hunk, index) => <Hunk key={`${index}:${hunk.content}`} hunk={hunk} />)}
      </Diff> : <p className="p-3 text-slate-400">{file.operation === 'unchanged' ? '文件内容没有变化。' : file.operation === 'created' && file.after_bytes === 0 ? '已创建空文件。' : '本次未保留差异正文，请查看采集状态。'}</p>}
    </div>
  </section>
}

/** 显示本次调用的私有文件节点，不读取当前工作区，也不提供应用/回滚入口。
 * @param value 已授权、已限长的 write 私有详情。
 * @param showFileHeading 单项保持原标题，多文件父节点可省去重复标题。
 */
export function WriteDiff({ value, showFileHeading = true }: { value: WriteDetails; showFileHeading?: boolean }) {
  if (value.version !== 1) return <p>当前版本不支持此差异格式。</p>
  const notices: Record<string, string> = {
    pending: '等待本次写入结束后采集差异。', not_recorded: '此调用未记录文件差异，无法补回。',
    not_executed: '本次写入未执行，没有已应用差异。', result_unconfirmed: '写入结果未确认，不能据此判断文件没有变化。',
    partial: '差异仅部分展示；增删统计来自完整计算，不等于当前显示行数。',
  }
  return <section aria-label="本次文件变更" className="mt-3">
    {notices[value.availability] && <p className="text-amber-300">{notices[value.availability]}</p>}
    {value.availability === 'unavailable' && <p className="text-amber-300">{REASONS[value.reason ?? ''] ?? '差异不可用，不代表没有修改。'}</p>}
    {value.files.map((file) => <FileNode key={file.id} file={file} showHeading={showFileHeading} />)}
  </section>
}
