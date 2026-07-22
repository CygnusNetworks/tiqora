/**
 * Minimal HTML → plain-text helpers for list previews (not a sanitizer).
 * The API stores article bodies HTML-escaped regardless of `is_html`
 * (`ArticleBodyRenderer` un-escapes the HTML branch itself before handing it
 * to the iframe) — so a plain-text preview built directly from a non-HTML
 * body still needs entity decoding, or it shows literal `&gt;`/`&amp;`.
 */

/**
 * Strip tags/entities from an HTML fragment for a short preview string.
 * Uses a detached DOM node so entities decode correctly; `<script>` content
 * inserted via `innerHTML` is inert (never executed) and only `textContent`
 * is read back out, so this is safe for untrusted article bodies.
 */
export function stripHtml(html: string): string {
  if (typeof document === "undefined") {
    return html.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
  }
  const div = document.createElement("div");
  div.innerHTML = html;
  return (div.textContent || "").replace(/\s+/g, " ").trim();
}

/**
 * Decode HTML entities in a plain-text (non-HTML) body without stripping
 * tags — a plain-text article can legitimately contain `<`/`>` as literal
 * text, so this must not run it through `stripHtml`'s tag-eating parse.
 * Same detached-node trick as `stripHtml`; falls back to a regex covering
 * the 5 basic entities when there is no `document` (SSR).
 */
export function decodeEntities(text: string): string {
  if (typeof document === "undefined") {
    return text
      .replace(/&amp;/g, "&")
      .replace(/&lt;/g, "<")
      .replace(/&gt;/g, ">")
      .replace(/&quot;/g, '"')
      .replace(/&#39;/g, "'");
  }
  const div = document.createElement("div");
  div.innerHTML = text;
  return div.textContent || "";
}
