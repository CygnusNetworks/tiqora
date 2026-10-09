/**
 * Ends the session through the single-sign-on logout endpoint. The endpoint
 * answers with a 303 redirect (to the identity provider's logout or back to the
 * login page), so it has to be a real top-level form POST that the browser
 * follows — a fetch could not follow a redirect to another site. The POST is
 * same-origin, which is what the backend's Origin check expects.
 */
export function submitSsoLogout(
  action: "/api/v1/auth/sso/logout" | "/api/portal/auth/sso/logout",
): void {
  const form = document.createElement("form");
  form.method = "post";
  form.action = action;
  form.style.display = "none";
  document.body.appendChild(form);
  form.submit();
}
