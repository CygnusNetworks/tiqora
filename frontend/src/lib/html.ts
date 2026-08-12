/**
 * Minimal HTML → plain-text helpers for list previews (not a sanitizer).
 * The API stores article bodies HTML-escaped regardless of `is_html`
 * (`ArticleBodyRenderer` un-escapes the HTML branch itself before handing it
 * to the iframe) — so a plain-text preview built directly from a non-HTML
 * body still needs entity decoding, or it shows literal `&gt;`/`&amp;`.
 *
 * Parsing happens in an inert document (see `parseInert`), so these are safe on
 * raw untrusted input and do not depend on the server having sanitised it first
 * (security review L-6).
 */

/**
 * Parse `html` into an inert document and return its root element.
 *
 * `document.createElement("div").innerHTML = html` looks detached but the node
 * belongs to the *live* document, so the parser still kicks off subresource
 * loads — `<img src=x onerror=...>` fires there. `DOMParser` builds a separate,
 * inert document that never loads or executes anything, which is what makes
 * these helpers safe for untrusted article bodies rather than merely safe for
 * the sanitised ones they happen to get today.
 */
function parseInert(html: string): HTMLElement | null {
  try {
    return new DOMParser().parseFromString(html, "text/html").body;
  } catch {
    return null;
  }
}

/**
 * Strip tags/entities from an HTML fragment for a short preview string.
 * Only `textContent` is read back out.
 */
export function stripHtml(html: string): string {
  const body = typeof DOMParser === "undefined" ? null : parseInert(html);
  if (body === null) {
    return html.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
  }
  return (body.textContent || "").replace(/\s+/g, " ").trim();
}

/**
 * Decode HTML entities in a plain-text (non-HTML) body without stripping
 * tags — a plain-text article can legitimately contain `<`/`>` as literal
 * text, so this must not run it through `stripHtml`'s tag-eating parse.
 * Same detached-node trick as `stripHtml`; falls back to a regex covering
 * the 5 basic entities when there is no `document` (SSR).
 */
export function decodeEntities(text: string): string {
  const body = typeof DOMParser === "undefined" ? null : parseInert(text);
  if (body === null) {
    return text
      .replace(/&amp;/g, "&")
      .replace(/&lt;/g, "<")
      .replace(/&gt;/g, ">")
      .replace(/&quot;/g, '"')
      .replace(/&#39;/g, "'");
  }
  return body.textContent || "";
}
