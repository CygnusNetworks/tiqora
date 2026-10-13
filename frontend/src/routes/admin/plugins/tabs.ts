export const PLUGIN_TABS = ["installed", "available", "repositories"] as const;
export type PluginTab = (typeof PLUGIN_TABS)[number];

/** The `tab` search param is JSON-parsed by the router, so anything may arrive: accept only the known strings. */
export function parsePluginTab(value: unknown): PluginTab {
  return typeof value === "string" && (PLUGIN_TABS as readonly string[]).includes(value)
    ? (value as PluginTab)
    : "installed";
}
