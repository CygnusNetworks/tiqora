/**
 * Safe linkification (R-XSS): only absolute http(s) URLs become links; everything else
 * stays text. Callers render the parts as text nodes and `<a>` elements via JSX.
 */

export type TextPart =
  | { kind: "text"; text: string }
  | { kind: "link"; text: string; href: string };

const MAX_URL = 2000;
const CANDIDATE = /https?:\/\/[^\s<>"'`]+/gi;

/** Normalised href for an http(s) URL without credentials, else null. For dynamic hrefs too. */
export function safeHref(url: string | null | undefined): string | null {
  if (!url || url.length > MAX_URL) return null;
  try {
    const u = new URL(url.trim());
    if (u.protocol !== "http:" && u.protocol !== "https:") return null;
    if (u.username || u.password || !u.hostname) return null;
    return u.href;
  } catch {
    return null;
  }
}

const count = (s: string, c: string) => s.split(c).length - 1;

/** Strips trailing sentence punctuation and unbalanced closing parentheses. */
function trimTrailing(url: string): string {
  for (;;) {
    const last = url[url.length - 1];
    if (last && ".,;:!?".includes(last)) url = url.slice(0, -1);
    else if (last === ")" && count(url, ")") > count(url, "(")) url = url.slice(0, -1);
    else return url;
  }
}

export function linkify(text: string): TextPart[] {
  const parts: TextPart[] = [];
  let pos = 0;
  const push = (t: string) => {
    if (!t) return;
    const prev = parts[parts.length - 1];
    if (prev?.kind === "text") prev.text += t;
    else parts.push({ kind: "text", text: t });
  };
  for (const m of text.matchAll(CANDIDATE)) {
    const start = m.index ?? 0;
    const url = trimTrailing(m[0]);
    const href = safeHref(url);
    push(text.slice(pos, start));
    if (href) parts.push({ kind: "link", text: url, href });
    else push(url);
    push(m[0].slice(url.length));
    pos = start + m[0].length;
  }
  push(text.slice(pos));
  return parts;
}
