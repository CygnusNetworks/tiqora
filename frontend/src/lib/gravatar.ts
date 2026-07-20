import { md5 } from "@/lib/md5";

/**
 * Build a Gravatar avatar URL for an email address.
 *
 * Mirrors the netadmin approach:
 *   email_hash = md5(email.strip().lower().encode("utf-8"))
 *   https://www.gravatar.com/avatar/{hash}?d=…&s=…
 *
 * `d=404` so a missing Gravatar returns HTTP 404 and the UI can fall back to
 * initials (see {@link Avatar}). Empty / whitespace-only emails yield `null`.
 */
export function gravatarUrl(
  email: string | null | undefined,
  opts: { size?: number; defaultParam?: string } = {},
): string | null {
  const normalized = (email ?? "").trim().toLowerCase();
  if (!normalized) return null;

  const size = opts.size ?? 80;
  const d = opts.defaultParam ?? "404";
  const hash = md5(normalized);
  return `https://www.gravatar.com/avatar/${hash}?d=${encodeURIComponent(d)}&s=${size}`;
}

/**
 * Best-effort email for the signed-in agent.
 * Prefer an explicit `email` field when the API exposes one; otherwise use
 * `login` when it looks like an address. Returns `undefined` if neither works.
 */
export function userEmailForAvatar(user: {
  email?: string | null;
  login?: string | null;
} | null | undefined): string | undefined {
  const explicit = user?.email?.trim();
  if (explicit) return explicit;
  const login = user?.login?.trim();
  if (login?.includes("@")) return login;
  return undefined;
}
