/**
 * Remembers how the agent last authenticated *in this browser*, so the login
 * page can pick the right re-auth path when a session expires.
 *
 * The server's `auth_method` on `/me` is `"session"` once a cookie exists — it
 * does not preserve the original mechanism — so we record it client-side at the
 * moment of login instead. On session expiry the agent is bounced to
 * `/login?next=…`; only a remembered `spnego` (Kerberos) login should silently
 * redirect back into the SPNEGO handshake. A password (or OIDC/LDAP) login must
 * land on the normal form, not the Kerberos flow.
 */
const KEY = "tiqora-login-method";

export type LoginMethod = "password" | "spnego" | "oidc";

export function rememberLoginMethod(method: LoginMethod): void {
  try {
    localStorage.setItem(KEY, method);
  } catch {
    /* private mode / storage disabled — degrade to no auto re-auth */
  }
}

export function getLoginMethod(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function clearLoginMethod(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}
