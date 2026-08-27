import type { FullConfig, FullResult, Reporter, TestCase, TestResult } from '@playwright/test/reporter'
import { chromium } from '@playwright/test'
import { createHash } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import { chmodSync, copyFileSync, mkdirSync, readFileSync, readdirSync, renameSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { localIso } from './log-run'

type Options = { mode: 'fake' | 'real' }
type Artifact = { test_file: string; test_name: string; test_id_hash: string; type: string; path: string; size: number; sha256: string }

/** Playwright 日志 v2 reporter：原子 summary 与浏览器产物索引。 */
export default class RoleplexLogReporter implements Reporter {
  private readonly mode: 'fake' | 'real'
  private readonly runDir = process.env.ROLEPLEX_LOG_RUN_DIR ?? ''
  private readonly startedAt = process.env.LOG_RUN_STARTED_AT ?? new Date().toISOString()
  private readonly startedMs = Date.now()
  private readonly outcomes = new Map<string, { status: string; file: string; name: string; hash: string; error?: string }>()
  private readonly artifacts: Artifact[] = []

  constructor(options: Options) { this.mode = options.mode }

  /** @param _config Playwright 本轮完整配置；启动阶段只需建立日志事实。 */
  onBegin(_config: FullConfig) {
    this.ensureDirectory(this.runDir)
    this.writeSummary({ status: 'running', passed: 0, failed: 0, skipped: 0 })
    this.writeArtifacts()
  }

  /**
   * 聚合测试终态并复制允许持久化的脱敏产物。
   * @param test 当前 Playwright 测试项。
   * @param result 当前测试项的执行结果与附件。
   */
  onTestEnd(test: TestCase, result: TestResult) {
    const identity = this.testIdentity(test)
    this.outcomes.set(test.id, {
      status: result.status,
      ...identity,
      error: result.error?.message ? this.redact(result.error.message).slice(-8192) : undefined,
    })
    for (const attachment of result.attachments) {
      if (!attachment.path) continue
      try {
        const extension = path.extname(attachment.path)
        if (attachment.name.includes('trace') || extension === '.zip') continue
        const normalizedExtension = extension.toLowerCase()
        const textual = ['.md', '.txt', '.json'].includes(normalizedExtension)
        const screenshot = ['.png', '.jpg', '.jpeg', '.webp'].includes(normalizedExtension)
        if (!textual && !screenshot) continue
        const kind = textual ? 'diagnostics' : 'screenshots'
        const filename = `${identity.name}_${identity.hash}${extension}`
        const relative = path.join('artifacts', kind, filename)
        const destination = path.join(this.runDir, relative)
        this.ensureDirectory(path.dirname(destination))
        if (textual) {
          writeFileSync(destination, this.redact(readFileSync(attachment.path, 'utf-8')), { encoding: 'utf-8', mode: 0o640 })
        } else {
          copyFileSync(attachment.path, destination)
        }
        this.restrictFile(destination)
        const data = readFileSync(destination)
        this.artifacts.push({
          test_file: identity.file,
          test_name: identity.name,
          test_id_hash: identity.hash,
          type: textual ? 'diagnostic' : 'screenshot',
          path: relative.replaceAll('\\', '/'),
          size: data.length,
          sha256: createHash('sha256').update(data).digest('hex'),
        })
      } catch {
        // 浏览器产物复制失败会在 summary 的测试结果中体现，不覆盖原测试结论。
      }
    }
  }

  /** @param result Playwright 整轮最终状态。 */
  onEnd(result: FullResult) {
    const values = [...this.outcomes.values()]
    const failed = values.filter((item) => !['passed', 'skipped'].includes(item.status))
    this.writeArtifacts()
    this.writeSummary({
      status: result.status === 'passed' ? 'passed' : (result.status === 'interrupted' ? 'interrupted' : 'failed'),
      passed: values.filter((item) => item.status === 'passed').length,
      failed: failed.length,
      skipped: values.filter((item) => item.status === 'skipped').length,
      failures: failed.map((item) => ({
        test_file: item.file, test_name: item.name, test_id_hash: item.hash, message: item.error,
      })),
    })
  }

  /** @param test 用于派生安全展示名和不可逆 hash 的测试项。 */
  private testIdentity(test: TestCase) {
    const file = path.relative(process.cwd(), test.location.file).replaceAll('\\', '/')
    const base = test.title.replace(/^test[_ -]?/i, '').replace(/[^A-Za-z0-9_-]+/g, '_').slice(0, 80) || 'unnamed'
    return { file, name: base, hash: createHash('sha256').update(test.id).digest('hex').slice(0, 8) }
  }

  private source() {
    const repositoryRoot = path.resolve(process.cwd(), '..')
    try {
      const head = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: repositoryRoot, encoding: 'utf-8' }).trim()
      const status = execFileSync('git', ['status', '--porcelain'], { cwd: repositoryRoot, encoding: 'utf-8' })
      const dirty = Boolean(status)
      const source: Record<string, unknown> = { git_available: true, git_head: head, git_dirty: dirty }
      if (dirty) {
        const digest = createHash('sha256').update(execFileSync('git', ['diff', '--binary', 'HEAD'], { cwd: repositoryRoot }))
        const untracked = execFileSync('git', ['ls-files', '--others', '--exclude-standard', '-z'], { cwd: repositoryRoot })
          .toString('utf-8').split('\0').filter(Boolean).sort()
        for (const filename of untracked) {
          digest.update(`\0path\0${filename}\0`)
          try { digest.update(readFileSync(path.join(repositoryRoot, filename))) } catch { /* 文件竞态只影响指纹，不中断测试 */ }
        }
        source.working_tree_hash = digest.digest('hex').slice(0, 8)
      }
      return source
    } catch {
      return { git_available: false }
    }
  }

  private environment() {
    const commandVersion = (command: string, args: string[]) => {
      try { return execFileSync(command, args, { encoding: 'utf-8' }).trim() } catch { return null }
    }
    return {
      backend_version: '0.1.0',
      python_version: commandVersion('python', ['--version']),
      node_version: process.version,
      operating_system: `${os.type()} ${os.release()}`,
      browser: 'chromium',
      browser_version: commandVersion(chromium.executablePath(), ['--version']),
    }
  }

  /** @param result 本轮 running 或最终计数。 */
  private writeSummary(result: Record<string, unknown>) {
    const backend = this.backendMetadata()
    const payload = {
      schema_version: 2,
      run_id: process.env.LOG_RUN_ID,
      run_kind: this.mode === 'fake' ? 'e2e-fake' : 'e2e-real',
      provider_mode: this.mode,
      source: this.source(),
      started_at: this.startedAt,
      ended_at: result.status === 'running' ? undefined : localIso(new Date()),
      duration_ms: Date.now() - this.startedMs,
      database: process.env.ROLEPLEX_E2E_DATABASE,
      worlds: process.env.ROLEPLEX_E2E_WORLDS?.split(',').filter(Boolean) ?? [],
      artifact_index: 'artifacts.json',
      ...backend,
      ...this.environment(),
      ...result,
    }
    this.atomicJson(path.join(this.runDir, 'summary.json'), payload)
  }

  private backendMetadata() {
    const processIds = new Set<string>()
    let providerType: string | undefined
    let model: string | undefined
    try {
      for (const filename of readdirSync(this.runDir).filter((name) => /^events(?:\.\d{3})?\.jsonl$/.test(name))) {
        for (const line of readFileSync(path.join(this.runDir, filename), 'utf-8').split(/\r?\n/)) {
          if (!line) continue
          try {
            const event = JSON.parse(line) as Record<string, unknown>
            if (typeof event.process_instance_id === 'string') processIds.add(event.process_instance_id)
            if (event.event === 'provider.built') {
              if (typeof event.provider_type === 'string') providerType = event.provider_type
              if (typeof event.model === 'string') model = event.model
            }
          } catch { /* 损坏日志由后端 reader 规则报告，reporter 不猜测修复 */ }
        }
      }
    } catch { /* summary 仍可完成 */ }
    return {
      backend_process_instance_ids: [...processIds],
      provider_type: providerType,
      model,
    }
  }

  private writeArtifacts() {
    this.atomicJson(path.join(this.runDir, 'artifacts.json'), { items: this.artifacts })
  }

  /**
   * 原子写入 JSON 文件并收紧本机权限。
   * @param destination 目标 JSON 路径。
   * @param payload 可序列化的安全元数据。
   */
  private atomicJson(destination: string, payload: unknown) {
    this.ensureDirectory(path.dirname(destination))
    const temporary = path.join(path.dirname(destination), `.${path.basename(destination)}.tmp-${process.pid}`)
    writeFileSync(temporary, JSON.stringify(payload, null, 2), { encoding: 'utf-8', mode: 0o640 })
    // renameSync 在同一目录内提供原子替换；Node/Windows 对已有目标支持由当前 LTS 验证。
    renameSync(temporary, destination)
    this.restrictFile(destination)
  }

  /** @param directory 需要创建并限制访问的日志目录。 */
  private ensureDirectory(directory: string) {
    mkdirSync(directory, { recursive: true, mode: 0o750 })
    if (process.platform !== 'win32') chmodSync(directory, 0o750)
  }

  /** @param filename 已创建、需要限制访问的日志文件。 */
  private restrictFile(filename: string) {
    if (process.platform !== 'win32') chmodSync(filename, 0o640)
  }

  /** @param value 可能包含凭据的浏览器诊断文本。 */
  private redact(value: string) {
    let result = value.replace(
      /(password|passwd|token|authorization|api[_-]?key|cookie)(\s*[=:]\s*)([^\s,;]+)/gi,
      '$1$2<redacted>',
    )
    for (const [key, secret] of Object.entries(process.env)) {
      if (secret && secret.length >= 4 && /(password|token|secret|key)/i.test(key)) {
        result = result.replaceAll(secret, '<redacted>')
      }
    }
    return result
  }
}
