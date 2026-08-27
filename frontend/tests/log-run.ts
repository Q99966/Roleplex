import { randomBytes } from 'node:crypto'
import path from 'node:path'

export type E2ELogMode = 'fake' | 'real'

/**
 * 返回带本地时区偏移的秒级 ISO 时间。
 * @param date 需要格式化的本地时间。
 */
export function localIso(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, '0')
  const offset = -date.getTimezoneOffset()
  const sign = offset >= 0 ? '+' : '-'
  const absolute = Math.abs(offset)
  return [
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`,
    `T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`,
    `${sign}${pad(Math.floor(absolute / 60))}:${pad(absolute % 60)}`,
  ].join('')
}

/**
 * 为当前 Playwright 进程创建一次共享日志运行身份。
 * @param mode 当前测试使用 fake 还是真实 provider。
 */
export function ensureE2ELogRun(mode: E2ELogMode) {
  process.env.LOG_RUN_ID ||= randomBytes(4).toString('hex')
  process.env.LOG_RUN_STARTED_AT ||= localIso(new Date())
  const started = new Date(process.env.LOG_RUN_STARTED_AT)
  const pad = (value: number) => String(value).padStart(2, '0')
  const day = `${started.getFullYear()}-${pad(started.getMonth() + 1)}-${pad(started.getDate())}`
  const time = `${pad(started.getHours())}-${pad(started.getMinutes())}-${pad(started.getSeconds())}`
  const runDir = path.resolve(
    process.cwd(), '../logs/tests/e2e', mode, day, `${time}_${process.env.LOG_RUN_ID}`,
  )
  process.env.ROLEPLEX_LOG_RUN_DIR = runDir
  return {
    runId: process.env.LOG_RUN_ID,
    startedAt: process.env.LOG_RUN_STARTED_AT,
    runDir,
  }
}
