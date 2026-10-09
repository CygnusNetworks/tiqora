function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** One value of a last_result entry. A nested result object (deploy_watch's
 * `process`/`acl` outcome: `{kind, action, revision_id, pending}`) reads as its
 * action plus open drafts; other objects as `key=value` pairs. */
function humanizeValue(v: unknown): string {
  if (Array.isArray(v)) return v.map(humanizeValue).join(", ");
  if (!isPlainObject(v)) return String(v);
  if (typeof v.action === "string") {
    const pending = typeof v.pending === "number" && v.pending > 0 ? ` (pending ${v.pending})` : "";
    return `${v.action}${pending}`;
  }
  return Object.entries(v)
    .map(([k, inner]) => `${k}=${isPlainObject(inner) || Array.isArray(inner) ? JSON.stringify(inner) : String(inner)}`)
    .join(", ");
}

/** Compact humanized form of the raw last_result JSON — "processed: 12" reads
 * better in a pill than {"processed":12}. Non-objects fall back to JSON. */
export function humanizeResult(result: unknown): string {
  if (result == null) return "";
  if (isPlainObject(result)) {
    const entries = Object.entries(result);
    if (entries.length > 0) {
      return entries.map(([k, v]) => `${k}: ${humanizeValue(v)}`).join(" · ");
    }
  }
  return JSON.stringify(result);
}
