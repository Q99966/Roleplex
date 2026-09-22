import type { WorkflowDefinition } from '../../api/workflows'

export type Draft = { runTarget?: string; definition: WorkflowDefinition; dirty: boolean; input: string; requestKey: string }
export type DraftView = { open: boolean; selected: string | null; protectedNodes: string[]; protectionEdited: boolean;
  target?: { mode: 'edit' | 'run'; runId: string | null; historyRevision: number | null } }
export type LocalDraft = { format: 1; id: string; scope: string; updatedAt: number; sequence: number; draft: Draft; view: DraftView; autoRestore?: boolean }
export type DraftStatus = 'saving' | 'saved' | 'error'
const DATABASE = 'roleplex-workflow-drafts'
const JOURNAL = 'roleplex:workflow-draft-journal:'
const HINT = 'roleplex:workflow-draft-active:'
let sequence: number = import.meta.hot?.data.sequence ?? 0
let database: Promise<IDBDatabase> | undefined

const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(item => typeof item === 'string')
const object = (value: unknown): value is Record<string, any> => Boolean(value && typeof value === 'object' && !Array.isArray(value))
/** 浏览器持久数据也需校验；允许未完成草稿，不把读取缓存当成服务端授权。 */
function valid(value: unknown, scope: string): value is LocalDraft {
  if (!object(value) || value.format !== 1 || value.scope !== scope || typeof value.id !== 'string'
    || !Number.isFinite(value.updatedAt) || !Number.isSafeInteger(value.sequence) || !object(value.draft) || !object(value.view)
    || (value.autoRestore !== undefined && typeof value.autoRestore !== 'boolean')) return false
  const { draft, view } = value
  const definition = draft.definition, graph = definition?.graph
  return typeof draft.dirty === 'boolean' && typeof draft.input === 'string' && typeof draft.requestKey === 'string'
    && (draft.runTarget === undefined || typeof draft.runTarget === 'string')
    && object(definition) && typeof definition.id === 'string' && typeof definition.name === 'string' && Number.isSafeInteger(definition.revision)
    && object(graph) && Array.isArray(graph.nodes) && graph.nodes.every((n: unknown) => object(n) && typeof n.id === 'string' && typeof n.title === 'string'
      && ['role','approval','join','condition','judge'].includes(n.kind) && Array.isArray(n.inputs) && n.inputs.every((x: unknown) => typeof x === 'string')
      && typeof n.task === 'string' && typeof n.expected_output === 'string'
      && (n.tools == null || strings(n.tools)) && (n.result_keys === undefined || strings(n.result_keys))
      && (n.result_schema === undefined || object(n.result_schema))
      && (n.color == null || typeof n.color === 'string')
      && (n.condition == null || (object(n.condition) && strings(n.condition.sources) && typeof n.condition.key === 'string'))
      && (!n.position || (Number.isFinite(n.position.x) && Number.isFinite(n.position.y))))
    && Array.isArray(graph.edges) && graph.edges.every((e: unknown) => Array.isArray(e) && e.length === 2 && e.every(x => typeof x === 'string'))
    && (graph.entries === undefined || strings(graph.entries))
    && (graph.loops === undefined || (Array.isArray(graph.loops) && graph.loops.every((loop: unknown) => object(loop)
      && typeof loop.id === 'string' && typeof loop.entry === 'string' && typeof loop.decision === 'string' && typeof loop.exit === 'string'
      && strings(loop.body) && strings(loop.carry_inputs))))
    && (graph.edge_rules === undefined || (Array.isArray(graph.edge_rules) && graph.edge_rules.every((rule: unknown) => object(rule)
      && typeof rule.source === 'string' && typeof rule.target === 'string' && ['always', 'true', 'false'].includes(rule.when))))
    && (graph.presentation == null || (object(graph.presentation) && Array.isArray(graph.presentation.groups)
      && graph.presentation.groups.every((group: unknown) => object(group) && typeof group.id === 'string' && typeof group.title === 'string' && strings(group.node_ids))
      && Array.isArray(graph.presentation.edge_labels) && graph.presentation.edge_labels.every((label: unknown) => object(label)
        && typeof label.source === 'string' && typeof label.target === 'string' && typeof label.label === 'string')))
    && typeof view.open === 'boolean' && (view.selected === null || typeof view.selected === 'string')
    && (view.target === undefined || (object(view.target) && ['edit', 'run'].includes(view.target.mode)
      && (view.target.runId === null || typeof view.target.runId === 'string')
      && (view.target.historyRevision === null || (Number.isSafeInteger(view.target.historyRevision) && view.target.historyRevision >= 0))))
    && Array.isArray(view.protectedNodes) && view.protectedNodes.every((x: unknown) => typeof x === 'string') && typeof view.protectionEdited === 'boolean'
}

function openDatabase() {
  if (!database) database = new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open(DATABASE, 1)
    let failed = false
    request.onupgradeneeded = () => { const store = request.result.createObjectStore('drafts', { keyPath: 'id' }); store.createIndex('scope', 'scope') }
    const timer = window.setTimeout(() => { failed = true; reject(new Error('DRAFT_STORAGE_UNAVAILABLE')) }, 4000)
    request.onerror = request.onblocked = () => { failed = true; clearTimeout(timer); reject(new Error('DRAFT_STORAGE_UNAVAILABLE')) }
    request.onsuccess = () => {
      clearTimeout(timer)
      if (failed) { request.result.close(); return }
      request.result.onversionchange = () => { request.result.close(); database = undefined }
      resolve(request.result)
    }
  }).catch(error => { database = undefined; throw error })
  return database
}

async function put(record: LocalDraft) {
  const db = await openDatabase()
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction('drafts', 'readwrite'), store = tx.objectStore('drafts')
    const request = store.get(record.id)
    request.onsuccess = () => {
      // 同一页面的异步旧写入不能覆盖更新序号；不同页面使用不同 id。
      if (!request.result || request.result.sequence < record.sequence) store.put(record)
    }
    tx.oncomplete = () => resolve()
    tx.onabort = tx.onerror = () => reject(new Error('DRAFT_STORAGE_UNAVAILABLE'))
  })
}

function newer(a: LocalDraft, b: LocalDraft) { return a.id === b.id ? a.sequence > b.sequence : a.updatedAt > b.updatedAt }

/** 只在取得当前 Owner 的服务端作用域后读取；刷新优先恢复本标签页最后编辑的副本。 */
export async function readLocalDrafts(scope: string) {
  const records = new Map<string, LocalDraft>()
  let storageError = false, corrupt = false, hint: string | null = null
  try {
    const db = await openDatabase()
    const values = await new Promise<unknown[]>((resolve, reject) => {
      const tx = db.transaction('drafts', 'readonly'), request = tx.objectStore('drafts').index('scope').getAll(scope)
      request.onsuccess = () => resolve(request.result)
      request.onerror = () => reject(new Error('DRAFT_STORAGE_UNAVAILABLE'))
    })
    for (const value of values) { if (valid(value, scope)) records.set(value.id, value); else corrupt = true }
  } catch { storageError = true }
  try {
    hint = sessionStorage.getItem(HINT + scope)
    const text = sessionStorage.getItem(JOURNAL + scope)
    if (text) {
      const value: unknown = JSON.parse(text)
      if (valid(value, scope)) {
        const prior = records.get(value.id)
        if (!prior || newer(value, prior)) records.set(value.id, value)
      } else corrupt = true
    }
  } catch { storageError = true }
  const items = [...records.values()].sort((a, b) => b.updatedAt - a.updatedAt || b.sequence - a.sequence)
  return { items, preferred: items.find(r => r.id === hint) ?? items[0] ?? null, storageError, corrupt }
}

/** 只删除用户明确选择的副本，不清理其他标签页或其他身份的数据。 */
export async function deleteLocalDraft(record: LocalDraft) {
  const db = await openDatabase()
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction('drafts', 'readwrite'), store = tx.objectStore('drafts'), request = store.get(record.id)
    request.onsuccess = () => {
      if (request.result && request.result.sequence !== record.sequence) { tx.abort(); return }
      store.delete(record.id)
    }
    tx.oncomplete = () => resolve()
    tx.onabort = tx.onerror = () => reject(new Error('DRAFT_CHANGED'))
  })
  try {
    const text = sessionStorage.getItem(JOURNAL + record.scope)
    if (text) { const cached = JSON.parse(text); if (cached.id === record.id && cached.sequence === record.sequence) sessionStorage.removeItem(JOURNAL + record.scope) }
  } catch { /* IDB 的明确删除已完成，不输出私有存储内容。 */ }
}

const writers = new Set<DraftWriter>()
/** 合并连续编辑的异步落盘；离开/隐藏/HMR 时补写本标签页的同步恢复日志。 */
export class DraftWriter {
  private editorId = crypto.randomUUID()
  private timer: number | undefined
  private current: LocalDraft | null = null
  private persisted = 0
  private busy = false
  private disposed = false
  constructor(readonly scope: string, private notify: (status: DraftStatus, at?: number) => void) { writers.add(this) }
  schedule(draft: Draft, view: DraftView) {
    if (this.disposed) return
    const target = draft.runTarget ? `run:${draft.runTarget}` : `definition:${draft.definition.id}`
    const id = JSON.stringify([this.scope, this.editorId, target])
    // 目标切换前交接旧副本；不会因为用户选择另一流程就丢失原本的恢复点。
    if (this.current && this.current.id !== id && this.current.sequence > this.persisted) {
      this.emergency()
      void put(this.current).catch(() => { if (!this.disposed) this.notify('error') })
    }
    this.current = { format: 1, id, scope: this.scope, updatedAt: Date.now(), sequence: ++sequence, draft, view }
    this.notify('saving')
    // 固定合并窗口，连续输入不能无限推迟持久化。
    if (this.timer === undefined) this.timer = window.setTimeout(() => void this.flush(), 200)
  }
  /** 交接前持久保留旧编辑；另存后源草稿只供手动恢复，不能重新覆盖原模板。 */
  async preserve(manualOnly = false): Promise<boolean> {
    if (manualOnly && this.current) this.current = { ...this.current, autoRestore: false, sequence: ++sequence, updatedAt: Date.now() }
    this.emergency()
    try {
      while (this.current && this.current.sequence > this.persisted) {
        const record = this.current
        await put(record)
        this.persisted = Math.max(this.persisted, record.sequence)
      }
      this.editorId = crypto.randomUUID()
      return true
    } catch { if (!this.disposed) this.notify('error'); return false }
  }
  async flush() {
    clearTimeout(this.timer); this.timer = undefined
    if (this.busy || !this.current || this.current.sequence <= this.persisted) return
    this.busy = true
    const record = this.current
    try {
      await put(record)
      this.persisted = Math.max(this.persisted, record.sequence)
      try {
        sessionStorage.setItem(HINT + this.scope, record.id)
        const text = sessionStorage.getItem(JOURNAL + this.scope)
        if (text) { const cached = JSON.parse(text); if (cached.id === record.id && cached.sequence <= record.sequence) sessionStorage.removeItem(JOURNAL + this.scope) }
      } catch { /* 主存储已成功；备用日志不可用不伪装成主存储失败。 */ }
      if (this.current === record && !this.disposed) this.notify('saved', record.updatedAt)
    } catch { this.emergency(); if (!this.disposed) this.notify('error') }
    finally {
      this.busy = false
      if (this.current !== record) void this.flush()
    }
  }
  emergency() {
    if (!this.current || this.current.sequence <= this.persisted) return true
    try {
      sessionStorage.setItem(JOURNAL + this.scope, JSON.stringify(this.current))
      sessionStorage.setItem(HINT + this.scope, this.current.id)
      return true
    } catch { if (!this.disposed) this.notify('error'); return false }
  }
  dispose() { this.emergency(); void this.flush(); this.disposed = true; clearTimeout(this.timer); writers.delete(this) }
}

const flushOnExit = (event?: Event) => {
  for (const writer of writers) {
    const protectedDraft = writer.emergency(); void writer.flush()
    if (!protectedDraft && event?.type === 'beforeunload') { event.preventDefault(); (event as BeforeUnloadEvent).returnValue = '' }
  }
}
const flushOnHidden = () => { if (document.visibilityState === 'hidden') flushOnExit() }
window.addEventListener('pagehide', flushOnExit)
window.addEventListener('beforeunload', flushOnExit)
document.addEventListener('visibilitychange', flushOnHidden)
if (import.meta.hot) import.meta.hot.dispose(data => {
  flushOnExit(); data.sequence = sequence
  for (const writer of [...writers]) writer.dispose()
  window.removeEventListener('pagehide', flushOnExit)
  window.removeEventListener('beforeunload', flushOnExit)
  document.removeEventListener('visibilitychange', flushOnHidden)
})
