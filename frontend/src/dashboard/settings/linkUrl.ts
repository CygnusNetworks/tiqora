/**
 * Client mirror of the backend's `safe_link_url` (`tiqora.dashboard.settings_models`): `/path`,
 * `http(s)://…` and `mailto:` only. Both sides run `linkUrlCases.json`.
 *
 * Python's `str.strip()` and JavaScript's `trim()` disagree on U+0085 (Python strips it) and
 * U+FEFF (JavaScript strips it), so the "no surrounding whitespace" rule spells out Python's set.
 */
const PY_SPACE = "\\x09-\\x0d\\x1c-\\x20\\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
const PY_EDGE_SPACE = new RegExp(`^[${PY_SPACE}]|[${PY_SPACE}]$`);

export function isSafeLinkUrl(url: unknown): url is string {
  if (typeof url !== "string" || !url || PY_EDGE_SPACE.test(url)) return false;
  for (let i = 0; i < url.length; i++) {
    const code = url.charCodeAt(i);
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
