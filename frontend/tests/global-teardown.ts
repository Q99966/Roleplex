import { readdir, rm } from 'node:fs/promises'
import path from 'node:path'

/** 保留最近多少轮的端到端数据库供人工排查。 */
const KEEP_RUNS = 5

/**
 * 按轮次清理端到端数据库，只删除超出保留轮数的旧文件。
 *
 * 本轮数据库会被保留：测试账号与数据库同名带时间戳，跑完可以直接登录进去核对结果。
 * 删除失败属于预期情况——后端 webServer 在本钩子执行时尚未退出，仍持有 SQLite 的
 * -wal/-shm 句柄，残留文件由下一轮收走，这里不能让 EBUSY 变成测试失败。
 */
export default async function globalTeardown() {
  const dataDir = path.resolve(process.cwd(), '../data')
  let entries: string[]
  try {
    entries = await readdir(dataDir)
  } catch {
    return
  }

  // 文件名形如 roleplex-e2e-<时间戳>.db[-wal|-shm]，按轮次分组后按时间戳排序。
  const runs = [...new Set(
    entries
      .filter((name) => name.startsWith('roleplex-e2e-'))
      .map((name) => name.split('.db')[0]),
  )].sort()
  const stale = runs.slice(0, Math.max(0, runs.length - KEEP_RUNS))

  await Promise.all(
    entries
      .filter((name) => stale.some((run) => name.startsWith(`${run}.db`)))
      .map((name) => rm(path.join(dataDir, name), { force: true }).catch(() => undefined)),
  )
}
