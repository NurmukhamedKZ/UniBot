/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** API origin, e.g. https://api.workkeldi.com -- webapp is deployed separately (Vercel). */
  readonly VITE_API_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
