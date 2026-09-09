import { defineConfig } from '@playwright/test'
import { ensureE2ELogRun } from './tests/log-run'
import path from 'node:path'

const now = new Date()
process.env.ROLEPLEX_COMMAND_E2E_STAMP ||= [now.getFullYear(), now.getMonth() + 1, now.getDate(), now.getHours(), now.getMinutes(), now.getSeconds()]
  .map((value) => String(value).padStart(2, '0')).join('')
const stamp = process.env.ROLEPLEX_COMMAND_E2E_STAMP
const logRun = ensureE2ELogRun('fake')
process.env.ROLEPLEX_E2E_STAMP = stamp
process.env.ROLEPLEX_E2E_API_ORIGIN = 'http://127.0.0.1:8005'
process.env.ROLEPLEX_E2E_WORLDS = `data/roleplex-command-e2e-${stamp}/default`
process.env.ROLEPLEX_COMMAND_E2E_WORKSPACE = path.join(
  process.env.ROLEPLEX_E2E_WORKSPACE_ROOT || '/home/chen/workspace/testworkspace',
  `roleplex-command-e2e-${stamp}`, 'default',
)

export default defineConfig({
  testDir: './tests/commands', workers: 1, fullyParallel: false, timeout: 90_000,
  globalSetup: './tests/commands/global-setup.ts',
  reporter: [['list'], ['./tests/log-reporter.ts', { mode: 'fake' }]],
  use: { baseURL: 'http://127.0.0.1:51178', trace: 'off', video: 'off', screenshot: 'only-on-failure' },
  webServer: [
    {
      command: 'python tests/command_e2e_server.py', cwd: '../backend',
      env: { CORS_ORIGINS: 'http://127.0.0.1:51178', AGENT_USE_FAKE_PROVIDER: 'true', LOG_RUN_KIND: 'e2e-fake',
        LOG_RUN_ID: logRun.runId, LOG_RUN_STARTED_AT: logRun.startedAt, LOG_ARCHIVE_ENABLED: 'false' },
      url: 'http://127.0.0.1:8005/api/health', reuseExistingServer: false,
    },
    {
      command: 'npm run dev -- --host 127.0.0.1 --port 51178', cwd: '.',
      env: { VITE_PROXY_TARGET: 'http://127.0.0.1:8005' },
      url: 'http://127.0.0.1:51178', reuseExistingServer: false,
    },
  ],
})
