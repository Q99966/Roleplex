import { readdir, rm } from 'node:fs/promises'
import path from 'node:path'

const KEEP_RUNS = 5

/** 保留最近五轮世界切换 E2E 目录，供人工检查两个物理数据库。 */
export default async function globalTeardown() {
  const dataDir = path.resolve(process.cwd(), '../data')
  let entries: string[]
  try {
    entries = await readdir(dataDir)
  } catch {
    return
  }
  const runs = entries.filter((name) => /^roleplex-world-e2e-\d{14}$/.test(name)).sort()
  const stale = runs.slice(0, Math.max(0, runs.length - KEEP_RUNS))
  await Promise.all(stale.map((name) => rm(path.join(dataDir, name), { recursive: true, force: true }).catch(() => undefined)))

  const workspaceRoot = process.env.ROLEPLEX_E2E_WORKSPACE_ROOT
  if (!workspaceRoot || path.resolve(workspaceRoot) === path.parse(path.resolve(workspaceRoot)).root) return
  let workspaceEntries: string[]
  try {
    workspaceEntries = await readdir(workspaceRoot)
  } catch {
    return
  }
  const workspaceRuns = workspaceEntries.filter((name) => /^roleplex-world-e2e-\d{14}$/.test(name)).sort()
  const staleWorkspaces = workspaceRuns.slice(0, Math.max(0, workspaceRuns.length - KEEP_RUNS))
  await Promise.all(staleWorkspaces.map((name) => (
    rm(path.join(workspaceRoot, name), { recursive: true, force: true }).catch(() => undefined)
  )))
}
