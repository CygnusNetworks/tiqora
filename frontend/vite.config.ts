import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
import { PLUGIN_SHARED_MODULES } from "../packages/plugin-sdk/src/shared";

// Backend the dev server proxies to. Defaults to the host backend on
// localhost:8000; set VITE_PROXY_TARGET (e.g. http://tiqora-api:8000) when the
// dev server runs in a container against the compose backend.
const proxyTarget = process.env.VITE_PROXY_TARGET || "http://localhost:8000";
// Bind-mounted source on Docker Desktop (macOS/Windows) often needs polling for
// the file watcher to see changes — opt in via VITE_USE_POLLING=1.
const usePolling = process.env.VITE_USE_POLLING === "1";

// Plugin runtime shims (see packages/plugin-sdk/src/shared.ts): extra build
// entries emitted unhashed as dist/plugin-runtime/<file>.js. They import the
// same vendor chunks as the app, so plugins share its React/Query/Router/i18n.
const pluginRuntimeInputs = Object.fromEntries(
  PLUGIN_SHARED_MODULES.map((m) => [
    `plugin-runtime/${m.file}`,
    path.resolve(__dirname, `src/plugins/runtime/${m.file}.ts`),
  ]),
);

/** Dev server: answer /plugin-runtime/<file>.js from the shim source. */
function pluginRuntimeDev(): Plugin {
  return {
    name: "tiqora-plugin-runtime-dev",
    apply: "serve",
    configureServer(server) {
      server.middlewares.use((req, _res, next) => {
        if (req.url) {
          req.url = req.url.replace(/^\/plugin-runtime\/([a-z0-9-]+)\.js(?=$|\?)/, "/src/plugins/runtime/$1.ts");
        }
        next();
      });
    },
  };
}

export default defineConfig({
  // Demo builds are served under a GitHub Pages project sub-path; set it via
  // VITE_BASE (e.g. "/tiqora/"). Normal builds stay at root "/".
  base: process.env.VITE_BASE || "/",
  plugins: [react(), pluginRuntimeDev()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": proxyTarget,
      "/health": proxyTarget,
      "/ready": proxyTarget,
      "/metrics": proxyTarget,
    },
    ...(usePolling ? { watch: { usePolling: true, interval: 200 } } : {}),
  },
  build: {
    outDir: "dist",
    sourcemap: true,
    rollupOptions: {
      input: {
        main: path.resolve(__dirname, "index.html"),
        ...pluginRuntimeInputs,
      },
      // Keep the shims' named exports (plugins import them by name).
      preserveEntrySignatures: "exports-only",
      output: {
        entryFileNames: (chunk) =>
          chunk.name.startsWith("plugin-runtime/") ? "[name].js" : "assets/[name]-[hash].js",
      },
    },
  },
});
