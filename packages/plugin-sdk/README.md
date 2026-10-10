# @tiqora/plugin-sdk

## What it is

The frontend half of the Tiqora plugin SDK (the Python half is `tiqora_sdk`).
It holds the TypeScript types a plugin's frontend code uses (`PluginDefinition`,
`PluginApi`, `RoutePageProps`, `TicketPanelProps`, `WidgetProps`, …) and
declares the runtime values (`definePlugin`, `usePluginContext`, `usePluginApi`,
`usePluginTranslation`, `Button`, `Badge`, `Spinner`, `Switch`, `Dialog`,
`Panel`, `EmptyState`, `SDK_VERSION`). The values are not in this package: the
Tiqora page provides them at `/plugin-runtime/sdk.js`, implemented by
`frontend/src/plugins/sdkRuntime.tsx`.

## The preset

`@tiqora/plugin-sdk/vite` exports `tiqoraPluginConfig({ entry, outDir })`, a
Vite library build that writes one ES module `index.js` (plus one CSS file)
into `outDir`, normally the plugin package's `web/` folder:

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { tiqoraPluginConfig } from "@tiqora/plugin-sdk/vite";

export default defineConfig({
  ...tiqoraPluginConfig({ entry: "src/index.tsx", outDir: "src/my_plugin/web" }),
  plugins: [react()],
});
```

`react`, `react/jsx-runtime`, `react-dom`, `@tanstack/react-query`,
`@tanstack/react-router`, `react-i18next` and `@tiqora/plugin-sdk` are not
bundled: they are imported from the page's runtime shims under
`/plugin-runtime/`, so a plugin uses the same React, query client, router and
translations as Tiqora itself. Import exactly these specifiers — a subpath such
as `react-dom/client` would be bundled as a second copy and break hooks.
Vite names the file `index.js` only when the plugin's `package.json` has
`"type": "module"` (otherwise `index.mjs`, which does not match the web
manifest's default `entry`). `pnpm check:preset` builds `test-plugin/` and
checks the result.

## Stability

`@tiqora/plugin-sdk` and `tiqora_sdk` are the only API a plugin may use. Both
carry the same version (`0.1.0`, also `SDK_VERSION`); tests on both sides pin
it and the exact surface. Until SDK 1.0, a 0.x minor may break plugins. Every
change to either surface bumps the version on both sides together and updates
the surface tests.
