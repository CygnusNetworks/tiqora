import type { UserConfig } from "vite";
import { PLUGIN_RUNTIME_PATH, PLUGIN_SHARED_MODULES } from "./shared";

export interface TiqoraPluginBuildOptions {
  /** Plugin entry module, e.g. "src/index.tsx" (relative to the Vite root). */
  entry: string;
  /** Output directory, normally the package's `web/` folder. */
  outDir: string;
}

/**
 * Vite config for a plugin's frontend bundle: one ES module `index.js`
 * (+ one CSS file), with every shared module (React, React Query, Router,
 * react-i18next, the SDK) imported from the page's runtime shims instead of
 * being bundled. Combine it with your own plugins:
 * `defineConfig({ ...tiqoraPluginConfig({ entry, outDir }), plugins: [react()] })`.
 */
export function tiqoraPluginConfig({ entry, outDir }: TiqoraPluginBuildOptions): UserConfig {
  return {
    // Library builds keep `process.env.NODE_ENV` as written; the browser has
    // no `process`, so a bundled dependency that reads it would crash.
    define: { "process.env.NODE_ENV": JSON.stringify("production") },
    build: {
      lib: { entry, formats: ["es"], fileName: "index" },
      outDir,
      emptyOutDir: true,
      cssCodeSplit: false,
      rollupOptions: {
        external: PLUGIN_SHARED_MODULES.map((m) => m.specifier),
        output: {
          paths: Object.fromEntries(
            PLUGIN_SHARED_MODULES.map((m) => [m.specifier, `${PLUGIN_RUNTIME_PATH}${m.file}.js`]),
          ),
          // No code-split chunks: web/ is always exactly index.js (+ CSS), so
          // nothing can get lost between build and wheel.
          inlineDynamicImports: true,
        },
      },
    },
  };
}
