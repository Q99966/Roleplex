import { defineConfig } from '@playwright/test'
import { ensureE2ELogRun } from './tests/log-run'

// 纯布局/投影测试无需启动浏览器和后端，沿用受控测试的摘要记录。
ensureE2ELogRun('fake')
export default defineConfig({
  testDir: './tests/workflow-logic', workers: 1,
  outputDir: 'test-results/workflow-logic',
  reporter: [['list'], ['./tests/log-reporter.ts', { mode: 'fake' }]],
})
