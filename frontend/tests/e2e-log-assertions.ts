import { access, readFile, readdir } from 'node:fs/promises'
import path from 'node:path'
import { expect } from '@playwright/test'

export type LogEvent = Record<string, unknown>

/** 读取本轮后端已经刷盘的 E2E JSONL 事件。 */
export async function readRunEvents(): Promise<LogEvent[]> {
  const runDir = process.env.ROLEPLEX_LOG_RUN_DIR
  if (!runDir) throw new Error('E2E 日志目录未初始化')
  const names = (await readdir(runDir)).filter((name) => /^events(?:\.\d{3})?\.jsonl$/.test(name)).sort()
  const events: LogEvent[] = []
  for (const name of names) {
    const content = await readFile(path.join(runDir, name), 'utf-8')
    for (const line of content.split(/\r?\n/)) {
      if (line) events.push(JSON.parse(line) as LogEvent)
    }
  }
  return events
}

/**
 * 等待当前 E2E 日志满足断言所需的事件数量。
 * @param predicate 选择目标事件的安全谓词。
 * @param count 至少需要出现的事件数。
 */
export async function waitForRunEvents(
  predicate: (event: LogEvent) => boolean,
  count: number,
): Promise<LogEvent[]> {
  let matched: LogEvent[] = []
  await expect.poll(async () => {
    matched = (await readRunEvents()).filter(predicate)
    return matched.length
  }, { timeout: 10_000 }).toBeGreaterThanOrEqual(count)
  return matched
}

/**
 * 验证受控世界目录包含可启动所需的数据库、元数据、双密钥和文件目录。
 * @param relativeWorld 从仓库根目录起算的测试世界相对路径。
 */
export async function expectManagedWorldLayout(relativeWorld: string): Promise<void> {
  const world = path.resolve(process.cwd(), '..', relativeWorld)
  for (const name of ['world.json', 'roleplex.db', '.jwt-secret', '.api-key-secret', 'files']) {
    await expect(access(path.join(world, name))).resolves.toBeUndefined()
  }
}

/**
 * 断言两轮上下文事件的稳定层未漂移且第二轮包含首轮终态历史。
 * @param events 按进程观察顺序排列的两条 context.loaded 事件。
 */
export function expectStableTwoTurnContext(events: LogEvent[]): void {
  expect(events).toHaveLength(2)
  for (const field of [
    'context_schema_version', 'runtime_prefix_hash', 'role_prefix_hash',
    'conversation_prefix_hash', 'tool_policy_hash',
  ]) {
    expect(events[1][field]).toBe(events[0][field])
  }
  expect(events.map((event) => event.context_message_count)).toEqual([0, 2])
  expect(events.every((event) => event.checkpoint_hash === undefined)).toBe(true)
}
