/** Client mirror of the backend's `safe_link_url`: `/path`, `http(s)://…` and `mailto:` only. */
export function isSafeLinkUrl(url: unknown): url is string {
  if (typeof url !== "string" || !url || url !== url.trim()) return false;
  for (const ch of url) {
    const code = ch.charCodeAt(0);
    if (code < 0x20 || code === 0x7f) return false;
  }
  if (url.startsWith("/")) return !url.startsWith("//") && !url.includes("\\");
  const i = url.indexOf(":");
  if (i < 0) return false;
  const scheme = url.slice(0, i).toLowerCase();
  const rest = url.slice(i + 1);
  return ["http", "https", "mailto"].includes(scheme) && (rest.startsWith("//") ? rest.slice(2) : rest) !== "";
}

/** How a safe url opens: in the app, in a new tab, or the mail client. */
export function linkKind(url: string): "internal" | "external" | "mailto" {
  if (url.startsWith("/")) return "internal";
  return url.toLowerCase().startsWith("mailto:") ? "mailto" : "external";
}
