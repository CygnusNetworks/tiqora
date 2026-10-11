import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { tiqoraPluginConfig } from "../src/vite";

// Run from packages/plugin-sdk: `vite build -c test-plugin/vite.config.ts`.
export default defineConfig({
  ...tiqoraPluginConfig({ entry: "test-plugin/src/index.tsx", outDir: "test-plugin/dist" }),
  plugins: [react()],
});
