/** Pure helpers of the ITSM components (kept out of the component files for fast refresh). */
import { ciDetailHref, type CiSource } from "./itsmRoutes";

const SAFE_SCHEMES = new Set(["http:", "https:", "mailto:"]);

/** The URL when it is http(s)/mailto, else null: attribute values are Znuny data, never trusted as links. */
export function safeHref(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const text = raw.trim();
  try {
    return SAFE_SCHEMES.has(new URL(text).protocol) ? text : null;
  } catch {
    return null;
  }
}

/** `"znuny:12"` / `"tiqora:7"` -> detail href; null for anything else. */
export function ciRefHref(ref: string | null | undefined): string | null {
  const m = /^(znuny|tiqora):([0-9]+)$/.exec(ref ?? "");
  return m ? ciDetailHref(m[1] as CiSource, Number(m[2])) : null;
}

/** Colour token family of an incident/deployment state, by the catalogue's `functionality`. */
export type CiStateColor = "green" | "warn" | "danger" | "muted";

const COLOR_BY_FUNCTIONALITY: Record<string, CiStateColor> = {
  operational: "green",
  warning: "warn",
  incident: "danger",
};

/** operational -> green, warning -> warn, incident -> danger; anything else is neutral. */
export function ciStateColor(functionality: string | null | undefined): CiStateColor {
  return (functionality && COLOR_BY_FUNCTIONALITY[functionality]) || "muted";
}
