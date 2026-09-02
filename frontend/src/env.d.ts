/// <reference types="vite/client" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue'

  const component: DefineComponent
  export default component
}

interface ImportMetaEnv {
  readonly VITE_DINGTALK_REMOTE_DEBUG?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
