import { useEffect, useRef, useState } from 'react'
import { api, getAuthEpoch } from '../api/client'
import { useAppStore } from '../store/app'

/** 当前 World 的敏感备份入口；明确确认后先回收，再触发本机下载，不缓存 ZIP。 */
export function WorldBackup() {
  const world = useAppStore((state) => state.worldName)
  const user = useAppStore((state) => state.user)
  const [supported, setSupported] = useState(false)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const request = useRef<AbortController | null>(null)
  useEffect(() => {
    let active = true
    setSupported(false); setNotice(''); setBusy(false)
    api.health().then((result) => { if (active) setSupported(result.world_managed && result.world_name === world) }).catch(() => undefined)
    return () => { active = false; request.current?.abort() }
  }, [world, user?.id])
  /** 确认会停止哪些实例；身份变化后丢弃迟到结果，不能向新会话下载旧 World 密钥。 */
  async function backup() {
    const epoch = getAuthEpoch()
    setBusy(true); setNotice('')
    const controller = new AbortController()
    request.current = controller
    try {
      const preview = await api.cleanupPreview('world', 0)
      if (controller.signal.aborted || epoch !== getAuthEpoch()) return
      const targets = preview.items.map((item) => `会话 ${item.conversation_id} · ${item.tool_name} · ${item.id.slice(0, 8)}`).join('\n')
      if (!confirm(`备份世界“${world}”将停止 ${preview.items.length} 个已登记实例，已停止的服务不会自动重启。\n${targets}\n备份包含聊天记录、密钥与凭据，请妥善保管，不要分享。外部工作区文件不包含在内。\n继续回收并下载备份吗？`)) return
      const blob = await api.backupWorld(controller.signal)
      if (controller.signal.aborted || epoch !== getAuthEpoch()) return
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url; link.download = `${world}-backup.zip`
      link.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
      setNotice('备份已生成并开始下载；原服务保持停止，需要时重新审批启动。')
    } catch (error) {
      if (!controller.signal.aborted && epoch === getAuthEpoch()) setNotice(error instanceof Error ? error.message : '备份失败')
    } finally {
      if (!controller.signal.aborted && epoch === getAuthEpoch()) setBusy(false)
    }
  }
  if (!user?.is_owner) return null
  return <section className="rounded-lg border border-slate-700 p-3 text-xs text-slate-300">
    <button type="button" disabled={!supported || busy} onClick={() => void backup()}
      className="rounded border border-amber-700 px-3 py-2 text-amber-300 disabled:opacity-40">
      {busy ? '正在回收并生成备份…' : '回收进程并备份当前世界'}
    </button>
    <p className="mt-2 text-slate-400">备份包含密钥，仅供 Owner 保管；不包含外部工作区目录。</p>
    {notice && <p role="status" className="mt-2">{notice}</p>}
  </section>
}
