import React from 'react'
import {createRoot} from 'react-dom/client'
import {flushSync} from 'react-dom'
import {Diff, Hunk, parseDiff} from 'react-diff-view'
import 'react-diff-view/style/index.css'

declare global { interface Window { renderDiff: (patch: string) => {parseMs: number; renderMs: number}; unsafeMarker?: boolean } }
const root = createRoot(document.getElementById('root')!)
/** 独立候选试验：只显示传入的占位 patch，不读取产品状态。
 * @param patch 测试进程生成的受控 Git 格式差异。
 */
window.renderDiff = (patch: string) => {
  const start = performance.now()
  const files = parseDiff(patch)
  const parsed = performance.now()
  flushSync(() => root.render(<details open><summary>workspace_write · 页面变更</summary>
    <div className="scroll">{files.map((file, index) => <Diff key={index} viewType="unified" diffType={file.type} hunks={file.hunks}>
      {hunks => hunks.map(hunk => <Hunk key={hunk.content} hunk={hunk} />)}
    </Diff>)}</div></details>))
  return {parseMs: parsed - start, renderMs: performance.now() - parsed}
}
