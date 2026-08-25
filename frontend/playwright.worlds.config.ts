import { defineConfig } from '@playwright/test'

const pad = (value: number) => String(value).padStart(2, '0')
const now = new Date()
process.env.ROLEPLEX_WORLD_E2E_STAMP ||= [
  now.getFullYear(), pad(now.getMonth() + 1), pad(now.getDate()),
  pad(now.getHours()), pad(now.getMinutes()), pad(now.getSeconds()),
].join('')
const STAMP = process.env.ROLEPLEX_WORLD_E2E_STAMP
const WORLD_ROOT = `../data/roleplex-world-e2e-${STAMP}`

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
  reporter: [['list']],
  use: {
    baseURL: WEB_ORIGIN,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
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
