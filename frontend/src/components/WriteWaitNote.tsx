import type { WriteWait } from '../api/client'

/** 展示固定等待失败原因，不把未知执行结果当成未执行。
 * @param value Owner 私有详情中的有界等待诊断。
 */
export function WriteWaitNote({ value }: { value: WriteWait }) {
  const labels: Record<WriteWait['reason'], string> = {
    queue_full: '写入等待队列已满。', queue_bytes: '等待中的写入内容已达到容量上限。',
    queue_timeout: '排队等待超过本次总等待预算。', lock_timeout: '等待工作区锁超过本次总等待预算。',
    closed: '写入执行器已关闭。',
  }
  return <p className="text-amber-300">{labels[value.reason] ?? '写入等待未完成。'}请结合各文件结果确认执行状态。</p>
}
