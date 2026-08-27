import { defineConfig } from '@playwright/test'
import { ensureE2ELogRun } from './tests/log-run'

const pad = (value: number) => String(value).padStart(2, '0')
const now = new Date()
process.env.ROLEPLEX_REAL_E2E_STAMP ||= [
  now.getFullYear(), pad(now.getMonth() + 1), pad(now.getDate()),
  pad(now.getHours()), pad(now.getMinutes()), pad(now.getSeconds()),
].join('')
const STAMP = process.env.ROLEPLEX_REAL_E2E_STAMP
const DATABASE = `roleplex-real-e2e-${STAMP}.db`
const LOG_RUN = ensureE2ELogRun('real')
process.env.ROLEPLEX_E2E_DATABASE = `data/${DATABASE}`

const WEB_PORT = 51175
const API_PORT = 8002
const WEB_ORIGIN = `http://127.0.0.1:${WEB_PORT}`
const API_ORIGIN = `http://127.0.0.1:${API_PORT}`

export default defineConfig({
  testDir: './tests/real',
  timeout: 120_000,
  fullyParallel: false,
  workers: 1,
  globalTeardown: './tests/real/global-teardown.ts',
  reporter: [['list'], ['./tests/log-reporter.ts', { mode: 'real' }]],
  use: {
    baseURL: WEB_ORIGIN,
    screenshot: 'only-on-failure',
    // 真实 Key 不进入浏览器，但真实模型回复仍不应被默认持久化到 trace/video。
    trace: 'off',
    video: 'off',
  },
  webServer: [
    {
      command: 'python tests/real_e2e_server.py',
      cwd: '../backend',
      env: {
        DATABASE_URL: `sqlite+aiosqlite:///../data/${DATABASE}`,
        CORS_ORIGINS: WEB_ORIGIN,
        AGENT_USE_FAKE_PROVIDER: 'false',
        // 真实 provider 会联网计费，日志必须与普通 fake E2E 分开归档。
        LOG_RUN_KIND: 'e2e-real',
        LOG_RUN_ID: LOG_RUN.runId,
        LOG_RUN_STARTED_AT: LOG_RUN.startedAt,
        ROLEPLEX_REAL_E2E_STAMP: STAMP,
        ROLEPLEX_REAL_E2E_API_PORT: String(API_PORT),
      },
      url: `${API_ORIGIN}/api/health`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `npm run dev -- --host 127.0.0.1 --port ${WEB_PORT}`,
      cwd: '.',
      env: { VITE_API_URL: API_ORIGIN },
      url: WEB_ORIGIN,
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
})
