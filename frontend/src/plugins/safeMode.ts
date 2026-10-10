/**
 * Browser safe mode for plugins: `?plugins=off` switches every plugin off for
 * this browser tab (sessionStorage, so it survives reloads and in-app
 * navigation until `?plugins=on` or the tab is closed). While it is on, the
 * page neither asks for the plugin list nor loads any plugin code.
 */
export const PLUGIN_SAFE_MODE_KEY = "tiqora.plugins.off";

/** Fallback for this page load when sessionStorage refuses the write. */
let memoryOff: boolean | null = null;

export function applySafeModeFromUrl(search: string = typeof window === "undefined" ? "" : window.location.search): void {
  const value = new URLSearchParams(search).get("plugins");
  if (value !== "off" && value !== "on") return;
  const off = value === "off";
  try {
    if (off) sessionStorage.setItem(PLUGIN_SAFE_MODE_KEY, "1");
    else sessionStorage.removeItem(PLUGIN_SAFE_MODE_KEY);
    memoryOff = null;
  } catch {
    memoryOff = off;
  }
}

export function isPluginSafeMode(): boolean {
  // This page load asked for it, but storage refused the write/removal.
  if (memoryOff !== null) return memoryOff;
  try {
    return sessionStorage.getItem(PLUGIN_SAFE_MODE_KEY) === "1";
  } catch {
    // Blocked storage counts as "not switched off".
    return false;
  }
}
