import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发时 /api 代理到 FastAPI，生产环境由反向代理承担（TODO: 部署时配置 nginx）
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
