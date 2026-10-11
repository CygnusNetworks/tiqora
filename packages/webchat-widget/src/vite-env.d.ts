/// <reference types="vite/client" />

declare module "virtual:webchat-locales" {
  /** Every language with a locale file (file names, e.g. `de`, `pt_BR`). */
  export const LANGS: readonly string[];
  /** Lazy loader per language; each resolves to its flattened `webchatWidget.*` texts. */
  export const load: Readonly<
    Record<string, () => Promise<{ default: Record<string, string> }>>
  >;
}

declare module "virtual:webchat-locale/*" {
  const messages: Record<string, string>;
  export default messages;
}
