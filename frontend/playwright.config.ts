import { defineConfig } from '@playwright/test'

// 每轮端到端测试使用独立数据库文件：Owner 是实例级单例，
// 复用开发数据库会让后注册账号变成 Guest 并在配置类接口上被拒绝。
//
// 清理只放在 globalTeardown：本轮结束时后端仍持有 SQLite 的 -wal/-shm 句柄，
// 删除会抛 EBUSY，残留文件由下一轮 teardown 收走，因此最多遗留一轮。
// 配置文件会被主进程和每个 worker 重复导入，禁止在此处做删除等破坏性副作用。
const E2E_DATABASE = `roleplex-e2e-${Date.now()}.db`

// 端到端测试使用独立前端端口，避免与开发中的 vite 服务抢占 51173；
// 后端 CORS 允许来源必须同步为该端口，否则浏览器请求会被 CORS 拒绝而表现为 "Failed to fetch"。
const E2E_WEB_PORT = 51174
const E2E_WEB_ORIGIN = `http://127.0.0.1:${E2E_WEB_PORT}`
const E2E_API_ORIGIN = 'http://127.0.0.1:8000'

export default defineConfig({
  testDir: './tests',
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  globalTeardown: './tests/global-teardown.ts',
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    // 端口与后端 CORS 允许来源保持一致，换端口会让浏览器请求被 CORS 拒绝。
    baseURL: E2E_WEB_ORIGIN,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    video: 'off',
  },
  webServer: [
    {
      command: 'python -m uvicorn app.main:app --host 127.0.0.1 --port 8000',
      cwd: '../backend',
      env: {
        DATABASE_URL: `sqlite+aiosqlite:///../data/${E2E_DATABASE}`,
        ROLEPLEX_E2E_DATABASE: E2E_DATABASE,
        CORS_ORIGINS: E2E_WEB_ORIGIN,
      },
      url: `${E2E_API_ORIGIN}/api/health`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `npm run dev -- --host 127.0.0.1 --port ${E2E_WEB_PORT}`,
      cwd: '.',
      // 显式指定后端地址，避免 localhost 在 Windows 上解析到 IPv6 而后端只监听 IPv4。
      env: { VITE_API_URL: E2E_API_ORIGIN },
      url: E2E_WEB_ORIGIN,
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
})
