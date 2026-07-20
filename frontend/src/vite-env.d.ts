/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Release tag the build was produced from (empty on untagged builds). */
  readonly VITE_APP_VERSION?: string;
  /** Full git commit sha the build was produced from (empty in dev). */
  readonly VITE_GIT_SHA?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
