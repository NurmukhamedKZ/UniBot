/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Placeholder per-package Tribute checkout links until an API endpoint exists for these. */
  readonly VITE_TRIBUTE_LINK_S?: string
  readonly VITE_TRIBUTE_LINK_M?: string
  readonly VITE_TRIBUTE_LINK_L?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
