/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_DINGTALK_REMOTE_DEBUG?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
