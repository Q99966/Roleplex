import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_')
  const proxyTarget = env.VITE_PROXY_TARGET || 'http://127.0.0.1:8000'
  const proxy = {
    '/api': {
      target: proxyTarget,
      changeOrigin: true,
      ws: true,
    },
  }

  return {
    plugins: [react()],
    server: {
      port: 51173,
      host: '0.0.0.0',
      proxy,
    },
    // 本地预览继续保持浏览器同源请求，避免生产构建在预览时退回错误端口。
    preview: {
      proxy,
    },
  }
})
