import { mkdir } from 'node:fs/promises'
import path from 'node:path'

/** 为真实 Provider W1a smoke 创建与本轮 default World 同 stamp 的外部空工作区。 */
export default async function globalSetup() {
  const stamp = process.env.ROLEPLEX_REAL_WORLD_E2E_STAMP
  const root = process.env.ROLEPLEX_E2E_WORKSPACE_ROOT
  if (!stamp || !root) throw new Error('real-world workspace test environment is incomplete')
  await mkdir(path.join(root, `roleplex-real-world-e2e-${stamp}`, 'default'), { recursive: true })
}
