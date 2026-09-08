import { mkdir } from 'node:fs/promises'
import path from 'node:path'

/** 为 fake managed-world 创建与 World 名一一对应的外部空工作区目录。 */
export default async function globalSetup() {
  const stamp = process.env.ROLEPLEX_WORLD_E2E_STAMP
  const root = process.env.ROLEPLEX_E2E_WORKSPACE_ROOT
  if (!stamp || !root) throw new Error('fake world workspace test environment is incomplete')
  const runRoot = path.join(root, `roleplex-world-e2e-${stamp}`)
  await Promise.all([
    mkdir(path.join(runRoot, 'alpha'), { recursive: true }),
    mkdir(path.join(runRoot, 'beta'), { recursive: true }),
  ])
}
