import { expect, it } from "vitest";
import { tiqoraPluginConfig } from "../../../packages/plugin-sdk/src/vite";
import { PLUGIN_SHARED_MODULES } from "../../../packages/plugin-sdk/src/shared";

it("externalises every shared module and maps it to the runtime path", () => {
  const cfg = tiqoraPluginConfig({ entry: "src/index.tsx", outDir: "out/web" });
  const rollup = cfg.build!.rollupOptions!;
  expect(rollup.external).toEqual(PLUGIN_SHARED_MODULES.map((m) => m.specifier));
  expect((rollup.output as { paths: Record<string, string> }).paths["react"]).toBe("/plugin-runtime/react.js");
  expect((rollup.output as { paths: Record<string, string> }).paths["@tiqora/plugin-sdk"]).toBe("/plugin-runtime/sdk.js");
  expect(cfg.build!.lib).toMatchObject({ formats: ["es"], fileName: "index" });
  expect(cfg.build!.outDir).toBe("out/web");
});

it("keeps the plugin bundle in one file (no code-split chunks the wheel could lose)", () => {
  const cfg = tiqoraPluginConfig({ entry: "src/index.tsx", outDir: "out/web" });
  const output = cfg.build!.rollupOptions!.output as { inlineDynamicImports?: boolean };
  expect(output.inlineDynamicImports).toBe(true);
  expect(cfg.build!.cssCodeSplit).toBe(false);
});
