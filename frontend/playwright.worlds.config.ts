import { defineConfig } from '@playwright/test'
import { ensureE2ELogRun } from './tests/log-run'

const pad = (value: number) => String(value).padStart(2, '0')
const now = new Date()
process.env.ROLEPLEX_WORLD_E2E_STAMP ||= [
  now.getFullYear(), pad(now.getMonth() + 1), pad(now.getDate()),
  pad(now.getHours()), pad(now.getMinutes()), pad(now.getSeconds()),
].join('')
const STAMP = process.env.ROLEPLEX_WORLD_E2E_STAMP
const WORLD_ROOT = `../data/roleplex-world-e2e-${STAMP}`
const LOG_RUN = ensureE2ELogRun('fake')
process.env.ROLEPLEX_E2E_WORLDS = `data/roleplex-world-e2e-${STAMP}/alpha,data/roleplex-world-e2e-${STAMP}/beta`

const WEB_PORT = 51176
const API_PORT = 8003
const WEB_ORIGIN = `http://127.0.0.1:${WEB_PORT}`
const API_ORIGIN = `http://127.0.0.1:${API_PORT}`
process.env.ROLEPLEX_E2E_STAMP = STAMP
process.env.ROLEPLEX_E2E_API_ORIGIN = API_ORIGIN

export default defineConfig({
  testDir: './tests/world-managed',
  timeout: 90_000,
  fullyParallel: false,
  workers: 1,
  globalTeardown: './tests/world-managed/global-teardown.ts',
  reporter: [['list'], ['./tests/log-reporter.ts', { mode: 'fake' }]],
  use: {
    baseURL: WEB_ORIGIN,
    screenshot: 'only-on-failure',
    // 世界切换同样经过登录流程，Trace 不得保存认证输入。
    trace: 'off',
    video: 'off',
  },
  webServer: [
    {
      command: `python scripts/run_world_server.py --world alpha --ensure-world beta --worlds-dir ${WORLD_ROOT} --host 127.0.0.1 --port ${API_PORT}`,
      cwd: '../backend',
      env: {
        CORS_ORIGINS: WEB_ORIGIN,
        AGENT_USE_FAKE_PROVIDER: 'true',
        LOG_RUN_KIND: 'e2e-fake',
        LOG_RUN_ID: LOG_RUN.runId,
        LOG_RUN_STARTED_AT: LOG_RUN.startedAt,
      },
      url: `${API_ORIGIN}/api/health`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `npm run dev -- --host 127.0.0.1 --port ${WEB_PORT}`,
      cwd: '.',
      // 世界切换测试也必须覆盖同源代理，避免直接 API 地址掩盖代理配置错误。
      env: { VITE_PROXY_TARGET: API_ORIGIN },
      url: WEB_ORIGIN,
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
})
