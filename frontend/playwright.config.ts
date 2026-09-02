import { defineConfig } from '@playwright/test'
import { ensureE2ELogRun } from './tests/log-run'

// 每轮端到端测试使用带时间戳的独立数据库：Owner 是实例级单例，
// 复用开发数据库会让后注册账号变成 Guest 并在配置类接口上被拒绝。
//
// 时间戳写进环境变量并且只在缺失时生成：配置文件会被主进程和每个 worker 重复导入，
// 每次重新取时间会让 worker 与主进程算出不同的库名和账号名。worker 由主进程派生，
// 因此能继承这里设置的值；测试账号名也据此派生，方便按轮次对照数据库内容。
// 使用本地时间，与后端测试库的时间戳口径保持一致。
const pad = (value: number) => String(value).padStart(2, '0')
const now = new Date()
process.env.ROLEPLEX_E2E_STAMP ||= [
  now.getFullYear(), pad(now.getMonth() + 1), pad(now.getDate()),
  pad(now.getHours()), pad(now.getMinutes()), pad(now.getSeconds()),
].join('')
const E2E_STAMP = process.env.ROLEPLEX_E2E_STAMP
const E2E_DATABASE = `roleplex-e2e-${E2E_STAMP}.db`
const LOG_RUN = ensureE2ELogRun('fake')
process.env.ROLEPLEX_E2E_DATABASE = `data/${E2E_DATABASE}`

// 端到端测试使用独立的前后端端口，避免与开发中的 vite / uvicorn 抢占 51173 与 8000；
// 后端 CORS 允许来源必须同步为该前端端口，否则浏览器请求会被 CORS 拒绝而表现为 "Failed to fetch"。
const E2E_WEB_PORT = 51174
const E2E_API_PORT = 8001
const E2E_WEB_ORIGIN = `http://127.0.0.1:${E2E_WEB_PORT}`
const E2E_API_ORIGIN = `http://127.0.0.1:${E2E_API_PORT}`

// 测试用例直接调后端 API 准备数据，这里把地址传给 worker，避免端口常量被复制到多处。
process.env.ROLEPLEX_E2E_API_ORIGIN = E2E_API_ORIGIN

// 弱口令账号无法通过注册接口创建，用例需要直接写库播种，因此把本轮数据库路径也传给 worker。
process.env.ROLEPLEX_E2E_DATABASE_PATH = `../data/${E2E_DATABASE}`

export default defineConfig({
  testDir: './tests',
  // 真实厂商测试必须通过独立配置显式运行，普通回归永不联网、不计费。
  testIgnore: ['**/real/**', '**/real-world/**', '**/world-managed/**'],
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  globalTeardown: './tests/global-teardown.ts',
  reporter: [['list'], ['html', { open: 'never' }], ['./tests/log-reporter.ts', { mode: 'fake' }]],
  use: {
    // 端口与后端 CORS 允许来源保持一致，换端口会让浏览器请求被 CORS 拒绝。
    baseURL: E2E_WEB_ORIGIN,
    screenshot: 'only-on-failure',
    // Trace 会记录认证输入和 DOM 状态，无法可靠脱敏，因此不持久化。
    trace: 'off',
    video: 'off',
  },
  webServer: [
    {
      command: `python -m uvicorn app.main:app --host 127.0.0.1 --port ${E2E_API_PORT}`,
      cwd: '../backend',
      env: {
        DATABASE_URL: `sqlite+aiosqlite:///../data/${E2E_DATABASE}`,
        ROLEPLEX_E2E_DATABASE: E2E_DATABASE,
        CORS_ORIGINS: E2E_WEB_ORIGIN,
        // 显式锁定确定性 fake provider：环境变量优先于 backend/.env，
        // 保证端到端测试既不联网也不消耗真实模型额度。
        AGENT_USE_FAKE_PROVIDER: 'true',
        // 浏览器链路日志独立归档，不与 pytest 或正常运行日志混在一起。
        LOG_RUN_KIND: 'e2e-fake',
        LOG_RUN_ID: LOG_RUN.runId,
        LOG_RUN_STARTED_AT: LOG_RUN.startedAt,
      },
      url: `${E2E_API_ORIGIN}/api/health`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `npm run dev -- --host 127.0.0.1 --port ${E2E_WEB_PORT}`,
      cwd: '.',
      // 浏览器保持同源访问 /api，由 Vite 代理到独立后端端口，覆盖实际开发代理链路。
      env: { VITE_PROXY_TARGET: E2E_API_ORIGIN },
      url: E2E_WEB_ORIGIN,
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
})
