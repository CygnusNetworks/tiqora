/** Minimal HTML → plain-text helpers for list previews (not a sanitizer). */

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
