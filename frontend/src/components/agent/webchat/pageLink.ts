export const MAX_LINK_CHARS = 60;

/** Host + path of an http(s) page URL for display, `null` for anything else
 * (`javascript:`, `data:`, garbage) — the visitor typed or influenced it. */
export function safePageLink(raw: string | null | undefined): { href: string; label: string } | null {
  if (!raw) return null;
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return null;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  const shown = url.host + (url.pathname === "/" ? "" : url.pathname);
  const label = shown.length > MAX_LINK_CHARS ? `${shown.slice(0, MAX_LINK_CHARS - 1)}…` : shown;
  return { href: url.href, label };
}

