import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发时 /api 代理到 FastAPI。
// 生产有两种形态，前端都走 '/api' 前缀，区别只在这里谁承担前缀剥离：
//   - nginx 形态（推荐）：deploy/nginx.conf 的 `proxy_pass http://api:8000/` 剥前缀，
//     语义与下面这行 rewrite 完全一致；
//   - 单容器形态：注入 VITE_API_BASE="" 让前端直接走同源根路径，不经过前缀。
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
