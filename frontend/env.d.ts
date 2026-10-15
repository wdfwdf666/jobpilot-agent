/// <reference types="vite/client" />

// 构建期注入的接口基址。单容器部署时 Dockerfile 传 VITE_API_BASE=""
// 走同源根路径；不定义则 api.ts 回落到 vite dev 代理的 '/api'。
interface ImportMetaEnv {
  readonly VITE_API_BASE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<{}, {}, any>
  export default component
}
