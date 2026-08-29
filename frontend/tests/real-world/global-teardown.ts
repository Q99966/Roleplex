import { readdir, rm } from 'node:fs/promises'
import path from 'node:path'

const KEEP_RUNS = 5

/** 保留最近五轮 real-world E2E 世界根目录，清理失败不覆盖测试结论。 */
export default async function globalTeardown() {
  const dataDir = path.resolve(process.cwd(), '../data')
  let entries: string[]
  try {
    entries = await readdir(dataDir)
  } catch {
    return
  }
  const runs = entries.filter((name) => name.startsWith('roleplex-real-world-e2e-')).sort()
  const stale = runs.slice(0, Math.max(0, runs.length - KEEP_RUNS))
  await Promise.all(stale.map((name) => rm(path.join(dataDir, name), { recursive: true, force: true }).catch(() => undefined)))
}
