import { mkdir, writeFile } from 'node:fs/promises'
import { randomBytes } from 'node:crypto'
import path from 'node:path'

/** 为真实 Provider W1a/W1b 创建与 World 同 stamp 的独立工作区和未知校验值。 */
export default async function globalSetup() {
  const stamp = process.env.ROLEPLEX_REAL_WORLD_E2E_STAMP
  const root = process.env.ROLEPLEX_E2E_WORKSPACE_ROOT
  if (!stamp || !root) throw new Error('real-world workspace test environment is incomplete')
  await mkdir(path.join(root, `roleplex-real-world-e2e-${stamp}`, 'default'), { recursive: true })
  const commandsRoot = path.join(root, `roleplex-real-world-e2e-${stamp}`, 'default', 'w1b-commands')
  await mkdir(commandsRoot)
  await writeFile(path.join(commandsRoot, 'command-proof.txt'), `W1B-${randomBytes(12).toString('hex')}\n第二行\n`, {
    flag: 'wx', encoding: 'utf-8', mode: 0o600,
  })
}
