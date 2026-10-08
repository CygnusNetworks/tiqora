/**
 * Error codes the backend appends as `?sso_error=<code>` when a single sign-on
 * ends on the login page. Mirrors `SSO_ERROR_CODES` in
 * `backend/src/tiqora/identity/http.py` (a test compares both lists).
 */
export const SSO_ERROR_CODES: readonly string[] = [
  "no_subject",
  "no_match",
  "user_invalid",
  "sso_not_enabled",
  "email_unverified",
  "ambiguous_email",
  "login_taken",
  "jit_missing_fields",
  "provider_disabled",
  "unknown_provider",
  "config",
  "saml_unavailable",
  "state",
  "saml_invalid",
  "oidc_failed",
  "idp_error",
];

/**
 * i18n key of the message for an `sso_error` value; `null` when there is no
 * error. Any non-empty value is an error: the legacy `1` (SPNEGO) and unknown
 * codes get the generic message.
 */
export function ssoErrorKey(code: string | undefined | null): string | null {
  if (!code) return null;
  return SSO_ERROR_CODES.includes(code)
    ? `auth.ssoErrors.${code}`
    : "auth.ssoFailed";
}
