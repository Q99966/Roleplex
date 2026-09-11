import {chromium} from '@playwright/test'
import {readFile} from 'node:fs/promises'
import {gzipSync} from 'node:zlib'
import {createTwoFilesPatch, diffLines} from 'diff'

const bundle = await readFile(new URL('./view.js', import.meta.url))
const css = await readFile(new URL('./view.css', import.meta.url), 'utf8')
const browser = await chromium.launch({headless:true})
const results = []
/** 为解析器补全固定文件头，不把库的格式要求当作产品协议。
 * @param before 占位旧文本。
 * @param after 占位新文本。
 */
const gitPatch = (before, after) => 'diff --git a/sample.ts b/sample.ts\n' + createTwoFilesPatch('a/sample.ts','b/sample.ts',before,after).replace(/^=+\n/, '')
try {
  const page = await browser.newPage({viewport:{width:1100,height:700}})
  await page.setContent('<html><head><meta charset="utf-8"></head><body><div id="root"></div></body></html>')
  await page.addStyleTag({content: css + 'body{background:#141417;color:#e4e4e7;font:14px monospace;padding:24px}summary{padding:16px;cursor:pointer}.scroll{max-height:540px;overflow:auto}.diff{--diff-background-color:#09090b;--diff-text-color:#e4e4e7;--diff-gutter-insert-background-color:#16412b;--diff-code-insert-background-color:#123322;--diff-gutter-delete-background-color:#562222;--diff-code-delete-background-color:#401b1b;}details{border:1px solid #3f3f46;border-radius:12px;overflow:hidden}'})
  await page.addScriptTag({content: bundle.toString()})
  for (const count of [10, 500, 1000]) {
    const before = Array.from({length:count},(_,i)=>`const v${i} = '旧内容';\n`).join('')
    const after = Array.from({length:count},(_,i)=>`const v${i} = '中文🙂';\n`).join('')
    const patch = gitPatch(before,after)
    const stats = await page.evaluate(patch=>window.renderDiff(patch),patch)
    results.push({rows:count*2,patchBytes:Buffer.byteLength(patch),...stats})
  }
  const unsafe = '<script>window.unsafeMarker=true</script>\n中文🙂\n'
  await page.evaluate(patch=>window.renderDiff(patch),gitPatch('旧内容\n',unsafe))
  if (await page.evaluate(()=>Boolean(window.unsafeMarker))) throw new Error('Unsafe HTML execution')
  if (!await page.locator('details').innerText().then(text=>text.includes('<script>')&&text.includes('中文🙂'))) throw new Error('Text not preserved')
  await page.getByText('workspace_write · 页面变更').click()
  if (await page.locator('.scroll').isVisible()) throw new Error('Collapse failed')
  await page.getByText('workspace_write · 页面变更').click()
  await page.screenshot({path:new URL('./candidate.png',import.meta.url).pathname})
  const started = performance.now()
  const aborted = diffLines('same\n'.repeat(4900),'other\n'+'same\n'.repeat(4899),{timeout:100,maxEditLength:20})
  console.log(JSON.stringify({candidate:'react-diff-view@3.3.3',react:'18.3.1',bundleBytes:bundle.length,gzipBytes:gzipSync(bundle).length,
    results,textSafety:true,collapse:true,jsdiff:{version:'9.0.0',timeMs:performance.now()-started,aborted:aborted===undefined}}))
} finally { await browser.close() }
