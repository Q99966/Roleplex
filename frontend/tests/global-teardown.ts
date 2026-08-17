import { readdir, rm } from 'node:fs/promises'
import path from 'node:path'

/**
 * 尽力清理端到端数据库文件。
 *
 * 后端 webServer 在本钩子执行时尚未退出，仍持有 SQLite 的 -wal/-shm 句柄，
 * 因此删除失败属于预期情况：残留文件由下一轮 playwright.config.ts 在
 * 启动 webServer 之前清理，这里不能让 EBUSY 变成测试失败。
 */
export default async function globalTeardown() {
  const dataDir = path.resolve(process.cwd(), '../data')
  let entries: string[]
  try {
    entries = await readdir(dataDir)
  } catch {
    return
  }
  await Promise.all(
    entries
      .filter((name) => name.startsWith('roleplex-e2e'))
      .map((name) => rm(path.join(dataDir, name), { force: true }).catch(() => undefined)),
  )
}
