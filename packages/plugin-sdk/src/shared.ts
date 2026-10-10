/**
 * Modules a plugin shares with the Tiqora page instead of bundling its own
 * copy. The host build emits one runtime shim per entry at
 * `PLUGIN_RUNTIME_PATH + file + ".js"`; the plugin preset marks every
 * specifier external and rewrites it to that URL.
 */
export const PLUGIN_RUNTIME_PATH = "/plugin-runtime/";
export const PLUGIN_SHARED_MODULES = [
  { specifier: "react", file: "react" },
  { specifier: "react/jsx-runtime", file: "react-jsx-runtime" },
  { specifier: "react-dom", file: "react-dom" },
  { specifier: "@tanstack/react-query", file: "react-query" },
  { specifier: "@tanstack/react-router", file: "react-router" },
  { specifier: "react-i18next", file: "react-i18next" },
  { specifier: "@tiqora/plugin-sdk", file: "sdk" },
] as const;
