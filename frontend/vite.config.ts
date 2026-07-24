import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// Backend the dev server proxies to. Defaults to the host backend on
// localhost:8000; set VITE_PROXY_TARGET (e.g. http://tiqora-api:8000) when the
// dev server runs in a container against the compose backend.
const proxyTarget = process.env.VITE_PROXY_TARGET || "http://localhost:8000";
// Bind-mounted source on Docker Desktop (macOS/Windows) often needs polling for
// the file watcher to see changes — opt in via VITE_USE_POLLING=1.
const usePolling = process.env.VITE_USE_POLLING === "1";

export default defineConfig({
  // Demo builds are served under a GitHub Pages project sub-path; set it via
  // VITE_BASE (e.g. "/tiqora/"). Normal builds stay at root "/".
  base: process.env.VITE_BASE || "/",
  plugins: [react()],
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
  },
});
