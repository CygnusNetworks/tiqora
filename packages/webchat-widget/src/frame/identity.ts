/**
 * Visitor sign-in helpers (C9). The signed token lives in memory only (a ref in App), never
 * in storage; these are the pure rules around it.
 */
import type { ChatPublicConfig } from "./api";
import type { StoredSession } from "./storage";

type Field = ChatPublicConfig["prechat_fields"][number];

/** Display-only name claim of a token (the server verifies the signature; this is never trusted). */
export function claimName(token: string): string | null {
  try {
    const part = token.split(".")[1] ?? "";
    const b64 = part.replace(/-/g, "+").replace(/_/g, "/");
    const bytes = Uint8Array.from(atob(b64 + "=".repeat((4 - (b64.length % 4)) % 4)), (c) => c.charCodeAt(0));
    const o = JSON.parse(new TextDecoder().decode(bytes)) as { name?: unknown } | null;
    const n = typeof o?.name === "string" ? o.name.trim() : "";
    return n ? n.slice(0, 100) : null;
  } catch {
    return null;
  }
}

/** With a pending identity the fields that carry name and e-mail are not asked again. */
export function visibleFields(fields: readonly Field[], signedIn: boolean): Field[] {
  return signedIn ? fields.filter((f) => f.key !== "name" && f.kind !== "email") : [...fields];
}

/**
 * Restore rule: a stored session of a signed-in visitor is restored only while the host (or
 * the portal) vouches for the visitor in this frame; otherwise somebody else may sit at the
 * browser and the stored chat is dropped.
 */
export function mayRestore(stored: Pick<StoredSession, "identified">, hasIdentity: boolean): boolean {
  return !stored.identified || hasIdentity;
}
