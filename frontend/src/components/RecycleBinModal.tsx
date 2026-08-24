import { useEffect, useState } from 'react'
import { Trash2, RotateCcw, X } from 'lucide-react'
import { useAppStore } from '../store/app'

/** 已删除会话的保留天数，与后端 `services/retention.py` 的 RETENTION_DAYS 一致。 */
const RETENTION_DAYS = 7

/**
 * 计算已删除会话的剩余保留天数。
 * @param deletedAt 服务端返回的删除时间（ISO 字符串）。
 * @returns 向上取整的剩余天数；已过期返回 0。
 */
function remainingDays(deletedAt: string | null): number {
  if (!deletedAt) return RETENTION_DAYS
  const elapsedMs = Date.now() - new Date(deletedAt).getTime()
  const remaining = RETENTION_DAYS - elapsedMs / 86_400_000
  return remaining > 0 ? Math.ceil(remaining) : 0
}

/**
 * 会话回收站：列出已删除会话、剩余保留天数并支持恢复。
 *
 * 删除只是软删除，数据在保留期内完整保留；超期后由服务端启动时清理。
 * 清理已经发生的会话不会出现在这里，因此列表里的每一条都还能恢复。
 */
export function RecycleBinModal({ onClose }: { onClose: () => void }) {
  const deletedConversations = useAppStore((state) => state.deletedConversations)
  const loadDeletedConversations = useAppStore((state) => state.loadDeletedConversations)
  const restoreConversation = useAppStore((state) => state.restoreConversation)
  const [loading, setLoading] = useState(true)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  useEffect(() => {
    void loadDeletedConversations()
      .catch(() => setErrorMsg('回收站加载失败'))
      .finally(() => setLoading(false))
  }, [loadDeletedConversations])

  /**
   * 恢复一个会话并刷新列表。
   * @param id 目标会话 id。
   */
  async function handleRestore(id: number) {
    setBusyId(id)
    setErrorMsg(null)
    try {
      await restoreConversation(id)
    } catch {
      setErrorMsg('恢复失败，该会话可能已超过保留期被清理')
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="fixed inset-0 bg-slate-950/80 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-slate-900 border border-slate-800 rounded-3xl w-full max-w-2xl overflow-hidden flex flex-col max-h-[85vh] shadow-2xl">
        <div className="px-6 py-5 border-b border-slate-800 flex items-center justify-between bg-slate-900/60">
          <div className="flex items-center gap-2 text-amber-400">
            <Trash2 size={18} />
            <h3 className="font-bold text-white text-base">回收站</h3>
          </div>
          <button onClick={onClose} className="p-1 rounded-lg text-slate-400 hover:bg-slate-800 hover:text-white transition">
            <X size={18} />
          </button>
        </div>

        <div className="px-6 py-4 overflow-y-auto">
          <p className="mb-4 text-xs text-slate-500">
            删除的会话会在这里保留 {RETENTION_DAYS} 天，期间可以随时恢复；超过保留期后在下次启动服务时清理。
          </p>

          {errorMsg && (
            <p className="mb-4 rounded-xl bg-red-500/10 border border-red-500/20 px-4 py-3 text-sm text-red-400">{errorMsg}</p>
          )}

          {loading && <p className="py-8 text-center text-sm text-slate-600">加载中…</p>}

          {!loading && deletedConversations.length === 0 && (
            <p className="py-10 text-center text-sm text-slate-600">回收站是空的</p>
          )}

          <ul className="space-y-2">
            {deletedConversations.map((conversation) => (
              <li
                key={conversation.id}
                data-testid="recycle-bin-item"
                className="flex items-center justify-between gap-3 rounded-xl border border-slate-800 bg-slate-950 px-4 py-3"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm text-slate-200">{conversation.title}</p>
                  <p className="text-[11px] text-slate-500">还可恢复 {remainingDays(conversation.deleted_at)} 天</p>
                </div>
                <button
                  type="button"
                  onClick={() => handleRestore(conversation.id)}
                  disabled={busyId === conversation.id}
                  className="flex items-center gap-1.5 shrink-0 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 transition hover:border-amber-500/40 hover:text-amber-300 disabled:opacity-50"
                >
                  <RotateCcw size={13} />
                  {busyId === conversation.id ? '恢复中…' : '恢复'}
                </button>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  )
}
