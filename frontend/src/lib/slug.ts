/**
 * Front-end slug preview. Mirrors the backend `slugify` (tiqora.kb.chunker):
 * lowercase, non-alphanumerics collapsed to single hyphens, trimmed. This is
 * only a preview — the backend remains the source of truth (it also resolves
 * collisions by appending `-2`, `-3`, …), so what the user sees may differ
 * slightly from the stored slug.
 */
export function slugify(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}
