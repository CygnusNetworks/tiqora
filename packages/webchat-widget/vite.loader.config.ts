import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

// Host-page loader: one classic IIFE script, served as /chat/v1/loader.js (C9).
// Built FIRST and empties dist/; the frame build adds frame.html + assets/ after it.
export default defineConfig({
  publicDir: false,
  build: {
    lib: {
      entry: fileURLToPath(new URL("src/loader/index.ts", import.meta.url)),
      formats: ["iife"],
      name: "TiqoraChatLoader",
      fileName: () => "loader.js",
    },
    outDir: "dist",
    emptyOutDir: true,
    target: "es2018",
  },
});
