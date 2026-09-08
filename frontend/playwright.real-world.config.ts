import { defineConfig } from '@playwright/test'
import { ensureE2ELogRun } from './tests/log-run'

const pad = (value: number) => String(value).padStart(2, '0')
const now = new Date()
process.env.ROLEPLEX_REAL_WORLD_E2E_STAMP ||= [
  now.getFullYear(), pad(now.getMonth() + 1), pad(now.getDate()),
  pad(now.getHours()), pad(now.getMinutes()), pad(now.getSeconds()),
].join('')
const STAMP = process.env.ROLEPLEX_REAL_WORLD_E2E_STAMP
const WORLD_ROOT = `../data/roleplex-real-world-e2e-${STAMP}`
const WORKSPACE_ROOT = process.env.ROLEPLEX_E2E_WORKSPACE_ROOT || '/home/chen/workspace/testworkspace'
const LOG_RUN = ensureE2ELogRun('real')

const WEB_PORT = 51177
const API_PORT = 8004
const WEB_ORIGIN = `http://127.0.0.1:${WEB_PORT}`
const API_ORIGIN = `http://127.0.0.1:${API_PORT}`
process.env.ROLEPLEX_E2E_API_ORIGIN = API_ORIGIN
process.env.ROLEPLEX_E2E_WORLDS = `data/roleplex-real-world-e2e-${STAMP}/default`
process.env.ROLEPLEX_E2E_WORKSPACE_ROOT = WORKSPACE_ROOT
process.env.ROLEPLEX_E2E_WORKSPACE_RELATIVE_ROOT = `roleplex-real-world-e2e-${STAMP}`

export default defineConfig({
  testDir: './tests/real-world',
  timeout: 150_000,
  fullyParallel: false,
  workers: 1,
  globalSetup: './tests/real-world/global-setup.ts',
  globalTeardown: './tests/real-world/global-teardown.ts',
  reporter: [['list'], ['./tests/log-reporter.ts', { mode: 'real' }]],
  use: {
    baseURL: WEB_ORIGIN,
    screenshot: 'only-on-failure',
    trace: 'off',
    video: 'off',
  },
  webServer: [
    {
      command: 'python tests/real_world_e2e_server.py',
      cwd: '../backend',
      env: {
        ROLEPLEX_REAL_E2E_STAMP: STAMP,
        ROLEPLEX_REAL_WORLD_E2E_ROOT: WORLD_ROOT,
        ROLEPLEX_REAL_WORLD_E2E_API_PORT: String(API_PORT),
        CORS_ORIGINS: WEB_ORIGIN,
        AGENT_USE_FAKE_PROVIDER: 'false',
        LOG_RUN_KIND: 'e2e-real',
        LOG_RUN_ID: LOG_RUN.runId,
        LOG_RUN_STARTED_AT: LOG_RUN.startedAt,
      },
      url: `${API_ORIGIN}/api/health`,
      reuseExistingServer: false,
      timeout: 90_000,
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
