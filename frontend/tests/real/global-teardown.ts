import { readdir, rm } from 'node:fs/promises'
import path from 'node:path'

const KEEP_RUNS = 5

/** 保留最近五轮真实 E2E 数据库，供人工登录核对；清理失败不覆盖测试结果。 */
export default async function globalTeardown() {
  const dataDir = path.resolve(process.cwd(), '../data')
  let entries: string[]
  try {
    entries = await readdir(dataDir)
  } catch {
    return
  }

  const runs = [...new Set(
    entries
      .filter((name) => name.startsWith('roleplex-real-e2e-'))
      .map((name) => name.split('.db')[0]),
  )].sort()
  const stale = runs.slice(0, Math.max(0, runs.length - KEEP_RUNS))

  await Promise.all(
    entries
      .filter((name) => stale.some((run) => name.startsWith(`${run}.db`)))
      .map((name) => rm(path.join(dataDir, name), { force: true }).catch(() => undefined)),
  )
}
