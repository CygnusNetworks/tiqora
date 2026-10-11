import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import { LOCALE_CHUNK_PREFIX, localeOfModule, widgetLocales } from "./scripts/widget-locales";

// Chat iframe: dist/frame.html + dist/assets/*, served by backend webchat/static.py as
// /chat/v1/frame and /chat/v1/assets/* (C9). The frame CSP has no 'unsafe-inline', so the
// HTML carries no inline script or style.
const here = (p: string) => fileURLToPath(new URL(p, import.meta.url));
const proxyTarget = process.env.VITE_PROXY_TARGET || "http://localhost:8000";

export default defineConfig({
  root: here("src/frame"),
  base: "/chat/v1/",
  publicDir: false,
  esbuild: { jsx: "automatic", jsxImportSource: "preact" },
  plugins: [widgetLocales({ localesDir: here("../../frontend/src/i18n/locales") })],
  server: {
    proxy: { "/api": proxyTarget },
  },
  build: {
    outDir: here("dist"),
    emptyOutDir: false,
    assetsDir: "assets",
    // check-size.mjs reads dist/.vite/manifest.json (R-BUDGET); static.py never serves it.
    manifest: true,
    // The polyfill fetch()es preloads, which the path-restricted connect-src would block.
    modulePreload: { polyfill: false },
    rollupOptions: {
      input: here("src/frame/frame.html"),
      output: {
        // One identifiable chunk per language: assets/webchat-locale-<lang>-<hash>.js
        chunkFileNames: (chunk) => {
          const lang = localeOfModule(chunk.facadeModuleId);
          return lang
            ? `assets/${LOCALE_CHUNK_PREFIX}${lang}-[hash].js`
            : "assets/[name]-[hash].js";
        },
      },
    },
  },
});
