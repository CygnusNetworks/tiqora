import { sha256 } from "@/lib/sha256";

/**
 * URLs of the backend avatar proxy (`/api/v1/avatars/…`). Pictures always
 * come from our own origin — the proxy fetches and caches Gravatar and
 * website favicons, so the page CSP stays `img-src 'self'` and no agent
 * browser talks to a third party. The key is the e-mail's SHA-256, never the
 * address itself, so it does not end up in URLs or access logs.
 */

const withVersion = (url: string, version?: string | null) =>
  version ? `${url}?v=${encodeURIComponent(version)}` : url;

/** Person picture (uploaded photo, else Gravatar) for an e-mail address. */
export function personAvatarUrl(
  email: string | null | undefined,
  version?: string | null,
): string | null {
  const normalized = (email ?? "").trim().toLowerCase();
  if (!normalized) return null;
  return withVersion(`/api/v1/avatars/email/${sha256(normalized)}`, version);
}

/** Company logo (uploaded logo, else website favicon) for a domain. */
export function domainLogoUrl(
  domain: string | null | undefined,
  version?: string | null,
): string | null {
  const normalized = (domain ?? "").trim().toLowerCase();
  if (!normalized) return null;
  return withVersion(`/api/v1/avatars/domain/${encodeURIComponent(normalized)}`, version);
}

/** Domain part of an e-mail address, lowercased; null without a usable one. */
export function emailDomain(email: string | null | undefined): string | null {
  const value = (email ?? "").trim().toLowerCase();
  const at = value.lastIndexOf("@");
  if (at < 1) return null;
  const domain = value.slice(at + 1);
  return domain.includes(".") ? domain : null;
}

/** Host of a company website (`customer_company.url`) without `www.`; the
 * scheme is optional, as agents type it either way. */
export function domainFromUrl(url: string | null | undefined): string | null {
  const value = (url ?? "").trim();
  if (!value) return null;
  try {
    const host = new URL(value.includes("://") ? value : `https://${value}`).hostname
      .toLowerCase()
      .replace(/^www\./, "");
    return host.includes(".") ? host : null;
  } catch {
    return null;
  }
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
