import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";
import { widgetLocales } from "./scripts/widget-locales";

const here = (p: string) => fileURLToPath(new URL(p, import.meta.url));

export default defineConfig({
  esbuild: { jsx: "automatic", jsxImportSource: "preact" },
  plugins: [widgetLocales({ localesDir: here("../../frontend/src/i18n/locales") })],
  test: {
    environment: "jsdom",
    environmentOptions: { jsdom: { url: "http://localhost/" } },
    include: ["src/**/*.{test,spec}.{ts,tsx}", "scripts/**/*.test.ts"],
  },
});
