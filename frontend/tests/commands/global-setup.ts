import { mkdir, writeFile, readdir, lstat, rm } from 'node:fs/promises'
import path from 'node:path'

/** 清理本测试拥有的准确目录，保留最近五轮。
 * @param parent 已确认的测试数据或工作区父目录。
 */
async function pruneRuns(parent: string) {
  const names = (await readdir(parent)).filter((name) => /^roleplex-command-e2e-\d{14}$/.test(name)).sort()
  for (const name of names.slice(0, Math.max(0, names.length - 5))) {
    const target = path.join(parent, name)
    const stat = await lstat(target)
    if (stat.isDirectory() && !stat.isSymbolicLink()) await rm(target, { recursive: true })
  }
}

/** 在每轮外部工作区创建无实际价值的受控文本夹具。 */
export default async function globalSetup() {
  const root = process.env.ROLEPLEX_COMMAND_E2E_WORKSPACE
  if (!root || !/roleplex-command-e2e-\d{14}/.test(root)) throw new Error('Missing isolated command workspace')
  const repository = path.resolve(process.cwd(), '..')
  if (!path.relative(repository, root).startsWith(`..${path.sep}`)) throw new Error('Workspace must be outside repository')
  await mkdir(root, { recursive: true })
  await pruneRuns(path.dirname(path.dirname(root)))
  await pruneRuns(path.join(repository, 'data'))
  for (const name of ['hello', 'output', 'exit', 'timeout', 'cancel']) {
    await writeFile(path.join(root, `${name}.txt`), '受控测试文本\n第二行', { flag: 'wx' })
  }
  await mkdir(path.join(root, 'timeline'))
  await writeFile(path.join(root, 'timeline', 'hello.txt'), 'TIMELINE-PRIVATE-PLACEHOLDER', { flag: 'wx' })
}
