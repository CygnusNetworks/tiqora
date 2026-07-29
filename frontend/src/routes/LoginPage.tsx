import { useEffect, useRef, useState, type FormEvent } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";
import { logoUrl } from "@/lib/assets";
import { getLoginMethod, rememberLoginMethod } from "@/lib/loginMethod";
import { api, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { OtpInput } from "@/components/ui/OtpInput";

function browserSupportsWebAuthn(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.PublicKeyCredential !== "undefined"
  );
}

/** Same-site absolute path only — reject protocol-relative (`//evil`) and `/\evil`. */
function isSafeNextPath(next: string | undefined): next is string {
  return (
    typeof next === "string" &&
    next.startsWith("/") &&
    !next.startsWith("//") &&
    !next.startsWith("/\\")
  );
}

/** Delay before auto-redirect into the SPNEGO handshake after session expiry. */
export const KERBEROS_REAUTH_DELAY_S = 30;

export function LoginPage() {
  const { t } = useTranslation();
  const {
    login,
    verifyTotp,
    verifyPasskey,
    completeEnroll2fa,
    completeEnrollPasskey,
    pending2fa,
    pendingFactors,
    mustEnroll2fa,
    isAuthenticated,
    isLoading,
  } = useAuth();
  const navigate = useNavigate();
  const search = useSearch({ from: "/login" }) as {
    next?: string;
    sso_error?: string;
  };
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [oidcEnabled, setOidcEnabled] = useState(false);
  const [spnegoEnabled, setSpnegoEnabled] = useState(false);
  const [webauthnEnabled, setWebauthnEnabled] = useState(false);

  // Forced enrollment step (must_enroll_2fa)
  const [enrollSecret, setEnrollSecret] = useState<string | null>(null);
  const [enrollQrNonce, setEnrollQrNonce] = useState(0);
  const [enrollCode, setEnrollCode] = useState("");
  const [enrollError, setEnrollError] = useState<string | null>(null);
  const [enrollStarting, setEnrollStarting] = useState(false);
  const [passkeyEnrolling, setPasskeyEnrolling] = useState(false);

  useEffect(() => {
    api
      .authMethods()
      .then((methods) => {
        setOidcEnabled(Boolean(methods.oidc));
        setSpnegoEnabled(Boolean(methods.spnego));
        setWebauthnEnabled(Boolean(methods.webauthn));
      })
      .catch(() => {
        setOidcEnabled(false);
        setSpnegoEnabled(false);
        setWebauthnEnabled(false);
      });
  }, []);

  useEffect(() => {
    if (!isLoading && isAuthenticated && !mustEnroll2fa && !pending2fa) {
      const next = isSafeNextPath(search.next) ? search.next : "/agent";
      void navigate({ to: next });
    }
  }, [
    isLoading,
    isAuthenticated,
    mustEnroll2fa,
    pending2fa,
    search.next,
    navigate,
  ]);

  // Seamless SSO re-auth: when an expired session bounced the agent here (a
  // `next` target is present) and Kerberos/SPNEGO is available, start a short
  // countdown then enter the handshake so a valid ticket lands them back
  // where they were. The delay gives the user time to notice and, if needed,
  // switch to password login instead of being yanked into Negotiate. `sso_error`
  // (set by a failed handshake) and the ref both prevent an auto-retry loop;
  // a plain visit to /login (no `next`) still shows the normal form with the
  // Kerberos button.
  const ssoErrorFlag = search.sso_error === "1" || search.sso_error === "true";
  const autoSsoTriggered = useRef(false);
  const reauthNextRef = useRef<string | undefined>(undefined);
  const [kerberosReauthSeconds, setKerberosReauthSeconds] = useState<
    number | null
  >(null);

  const startKerberosHandshake = (next?: string) => {
    rememberLoginMethod("spnego");
    window.location.assign(api.spnegoLoginUrl(next));
  };

  useEffect(() => {
    if (autoSsoTriggered.current) return;
    if (isLoading || isAuthenticated || pending2fa || mustEnroll2fa) return;
    if (ssoErrorFlag || !spnegoEnabled) return;
    if (!isSafeNextPath(search.next)) return;
    // Only auto re-auth via Kerberos if THIS browser's expired session was
    // itself started with SPNEGO. A password (or OIDC/LDAP) agent must see the
    // normal form instead of being bounced into the Kerberos handshake.
    if (getLoginMethod() !== "spnego") return;
    autoSsoTriggered.current = true;
    reauthNextRef.current = search.next;
    setKerberosReauthSeconds(KERBEROS_REAUTH_DELAY_S);
  }, [
    isLoading,
    isAuthenticated,
    pending2fa,
    mustEnroll2fa,
    ssoErrorFlag,
    spnegoEnabled,
    search.next,
  ]);

  // Tick the countdown once per second; redirect when it hits zero.
  useEffect(() => {
    if (kerberosReauthSeconds === null) return;
    if (kerberosReauthSeconds <= 0) {
      startKerberosHandshake(reauthNextRef.current);
      return;
    }
    const id = window.setTimeout(() => {
      setKerberosReauthSeconds((s) => (s === null ? null : s - 1));
    }, 1000);
    return () => window.clearTimeout(id);
  }, [kerberosReauthSeconds]);

  // Auto-start TOTP enrollment when forced into must-enroll mode (unless the
  // agent is mid passkey registration as the alternative path).
  useEffect(() => {
    if (!mustEnroll2fa || enrollSecret || enrollStarting || passkeyEnrolling)
      return;
    setEnrollStarting(true);
    setEnrollError(null);
    api
      .totpEnroll()
      .then((res) => {
        setEnrollSecret(res.secret);
        setEnrollQrNonce((n) => n + 1);
      })
      .catch(() => {
        setEnrollError(t("auth.mustEnroll.startError"));
      })
      .finally(() => setEnrollStarting(false));
  }, [mustEnroll2fa, enrollSecret, enrollStarting, passkeyEnrolling, t]);

  const goNext = async () => {
    const next = isSafeNextPath(search.next) ? search.next : "/agent";
    await navigate({ to: next });
  };

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(username, password);
      // pending2fa / mustEnroll2fa are set inside login(); navigation is
      // handled by the post-auth effect once a full session exists.
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError(t("auth.invalidCredentials"));
      } else {
        setError(t("auth.loginFailed"));
      }
    } finally {
      setSubmitting(false);
    }
  };

  const runVerifyTotp = async (code: string) => {
    if (submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      await verifyTotp(code);
      await goNext();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError(t("auth.totpInvalid"));
      } else {
        setError(t("auth.loginFailed"));
      }
    } finally {
      setSubmitting(false);
    }
  };

  const onVerifyTotp = async (e: FormEvent) => {
    e.preventDefault();
    await runVerifyTotp(totpCode);
  };

  const onVerifyPasskey = async () => {
    setError(null);
    setSubmitting(true);
    try {
      await verifyPasskey();
      await goNext();
    } catch (err) {
      if (
        err instanceof ApiError &&
        (err.status === 401 || err.status === 400)
      ) {
        setError(t("auth.passkeyInvalid"));
      } else {
        setError(t("auth.passkeyFailed"));
      }
    } finally {
      setSubmitting(false);
    }
  };

  const runConfirmEnroll = async (code: string) => {
    if (submitting) return;
    setEnrollError(null);
    setSubmitting(true);
    try {
      await completeEnroll2fa(code);
      await goNext();
    } catch {
      setEnrollError(t("auth.mustEnroll.confirmError"));
    } finally {
      setSubmitting(false);
    }
  };

  const onConfirmEnroll = async (e: FormEvent) => {
    e.preventDefault();
    await runConfirmEnroll(enrollCode);
  };

  const onEnrollPasskey = async () => {
    setEnrollError(null);
    setPasskeyEnrolling(true);
    setSubmitting(true);
    try {
      await completeEnrollPasskey(null);
      await goNext();
    } catch {
      setEnrollError(t("auth.passkeyEnrollFailed"));
    } finally {
      setSubmitting(false);
      setPasskeyEnrolling(false);
    }
  };

  // Only offer the factors this agent actually has enrolled (login response
  // flags); older/absent flags degrade to "offer both" so nobody locks out.
  const totpAvailable = pendingFactors ? pendingFactors.totp : true;
  const passkeyEnrolled = pendingFactors ? pendingFactors.passkey : true;
  const showPasskeyLogin = passkeyEnrolled && webauthnEnabled;
  const showPasskeyEnroll = webauthnEnabled && browserSupportsWebAuthn();

  if (mustEnroll2fa) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center bg-bg px-4">
        <div className="w-full max-w-sm rounded-xl border border-hairline bg-surface p-8">
          <h1 className="text-center font-display text-2xl font-bold tracking-tight text-ink">
            {t("auth.mustEnroll.title")}
          </h1>
          <p
            className="mt-1.5 text-center text-sm text-muted"
            data-testid="must-enroll-hint"
          >
            {t("auth.mustEnroll.hint")}
          </p>

          {enrollStarting && !enrollSecret && (
            <div className="mt-7 flex justify-center">
              <Spinner />
            </div>
          )}

          {enrollSecret && (
            <div className="mt-6 space-y-4" data-testid="must-enroll-step">
              <p className="text-sm text-muted">{t("security.scanHint")}</p>
              <img
                key={enrollQrNonce}
                src="/api/v1/auth/totp/enroll/qr"
                alt="TOTP QR code"
                width={200}
                height={200}
                data-testid="must-enroll-qr"
                className="mx-auto rounded-lg border border-hairline bg-white p-2"
              />
              <p className="text-xs text-muted">
                {t("security.secretLabel")}{" "}
                <code
                  data-testid="must-enroll-secret"
                  className="font-mono text-ink"
                >
                  {enrollSecret}
                </code>
              </p>
              <form
                onSubmit={(e) => void onConfirmEnroll(e)}
                className="space-y-3"
                data-testid="must-enroll-form"
              >
                <div className="text-sm">
                  <span className="mb-2 block text-muted">
                    {t("security.confirmCode")}
                  </span>
                  <OtpInput
                    data-testid="must-enroll-code"
                    aria-label={t("security.confirmCode")}
                    required
                    autoFocus
                    value={enrollCode}
                    onChange={(v) => {
                      setEnrollError(null);
                      setEnrollCode(v);
                    }}
                    onComplete={(code) => void runConfirmEnroll(code)}
                    status={
                      submitting && !passkeyEnrolling
                        ? "verifying"
                        : enrollError
                          ? "error"
                          : "idle"
                    }
                    statusMessage={enrollError}
                  />
                </div>
                <Button
                  type="submit"
                  variant="primary"
                  className="w-full"
                  disabled={submitting}
                  data-testid="must-enroll-submit"
                >
                  {submitting && !passkeyEnrolling ? (
                    <Spinner />
                  ) : (
                    t("security.confirmButton")
                  )}
                </Button>
              </form>

              {showPasskeyEnroll && (
                <>
                  <div className="flex items-center gap-3 text-xs text-muted">
                    <span className="h-px flex-1 bg-hairline" />
                    {t("auth.or")}
                    <span className="h-px flex-1 bg-hairline" />
                  </div>
                  <Button
                    type="button"
                    variant="secondary"
                    className="w-full"
                    disabled={submitting}
                    data-testid="must-enroll-passkey"
                    onClick={() => void onEnrollPasskey()}
                  >
                    {submitting && passkeyEnrolling ? (
                      <Spinner />
                    ) : (
                      t("auth.passkeyEnroll")
                    )}
                  </Button>
                </>
              )}
            </div>
          )}

          {enrollError && !enrollSecret && (
            <p
              className="mt-4 text-sm text-danger"
              role="alert"
              data-testid="must-enroll-error"
            >
              {enrollError}
            </p>
          )}
        </div>
      </div>
    );
  }

  if (pending2fa) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center bg-bg px-4">
        <div className="w-full max-w-sm rounded-xl border border-hairline bg-surface p-8">
          <h1 className="text-center font-display text-2xl font-bold tracking-tight text-ink">
            {totpAvailable ? t("auth.totpTitle") : t("auth.passkeyTitle")}
          </h1>
          <p className="mt-1.5 text-center text-sm text-muted">
            {totpAvailable ? t("auth.totpHint") : t("auth.passkeyOnlyHint")}
          </p>
          {totpAvailable && (
            <form
              onSubmit={(e) => void onVerifyTotp(e)}
              className="mt-7 space-y-4"
              data-testid="totp-form"
            >
              <div className="text-sm">
                <span className="mb-2 block text-muted">
                  {t("auth.totpCode")}
                </span>
                <OtpInput
                  data-testid="totp-code"
                  name="code"
                  aria-label={t("auth.totpCode")}
                  required
                  autoFocus
                  value={totpCode}
                  onChange={(v) => {
                    setError(null);
                    setTotpCode(v);
                  }}
                  onComplete={(code) => void runVerifyTotp(code)}
                  status={submitting ? "verifying" : error ? "error" : "idle"}
                  statusMessage={error}
                />
              </div>
              <Button
                type="submit"
                variant="primary"
                className="w-full"
                disabled={submitting}
                data-testid="totp-submit"
              >
                {submitting ? <Spinner /> : t("auth.totpVerify")}
              </Button>
            </form>
          )}

          {showPasskeyLogin && (
            <>
              {totpAvailable && (
                <div className="my-4 flex items-center gap-3 text-xs text-muted">
                  <span className="h-px flex-1 bg-hairline" />
                  {t("auth.or")}
                  <span className="h-px flex-1 bg-hairline" />
                </div>
              )}
              {!browserSupportsWebAuthn() && !totpAvailable && (
                <p
                  className="mt-4 text-sm text-danger"
                  role="alert"
                  data-testid="passkey-unsupported"
                >
                  {t("auth.passkeyUnsupported")}
                </p>
              )}
              {error && !totpAvailable && (
                <p
                  className="mt-4 text-sm text-danger"
                  data-testid="passkey-error"
                  role="alert"
                >
                  {error}
                </p>
              )}
              <Button
                type="button"
                variant={totpAvailable ? "secondary" : "primary"}
                className={totpAvailable ? "w-full" : "mt-6 w-full"}
                disabled={submitting}
                data-testid="passkey-login"
                onClick={() => void onVerifyPasskey()}
              >
                {submitting ? <Spinner /> : t("auth.passkeyLogin")}
              </Button>
            </>
          )}
        </div>
      </div>
    );
  }

  const showSsoDivider = oidcEnabled || spnegoEnabled;
  const ssoError = ssoErrorFlag;

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-bg px-4">
      <div className="w-full max-w-sm rounded-xl border border-hairline bg-surface p-8">
        <img
          src={logoUrl}
          alt=""
          width={36}
          height={36}
          className="mx-auto mb-3"
        />
        <h1 className="text-center font-display text-2xl font-bold tracking-tight text-ink">
          {t("app.name")}
        </h1>
        <p className="mt-1.5 text-center text-sm text-muted">
          {t("auth.signIn")}
        </p>
        {ssoError && (
          <p
            className="mt-3 rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger"
            role="alert"
            data-testid="sso-error"
          >
            {t("auth.ssoFailed")}
          </p>
        )}
        {kerberosReauthSeconds !== null && (
          <div
            className="mt-3 rounded-md border border-accent/30 bg-accent/10 px-3 py-3 text-sm text-ink"
            role="status"
            aria-live="polite"
            data-testid="kerberos-reauth-banner"
          >
            <p className="font-medium">{t("auth.kerberosReauthTitle")}</p>
            <p
              className="mt-1 text-muted"
              data-testid="kerberos-reauth-countdown"
            >
              {t("auth.kerberosReauthCountdown", {
                seconds: kerberosReauthSeconds,
              })}
            </p>
            <Button
              type="button"
              variant="secondary"
              className="mt-3 w-full"
              data-testid="kerberos-reauth-now"
              onClick={() => startKerberosHandshake(reauthNextRef.current)}
            >
              {t("auth.kerberosReauthNow")}
            </Button>
          </div>
        )}
        <form
          onSubmit={(e) => void onSubmit(e)}
          className="mt-7 space-y-4"
          data-testid="login-form"
        >
          <label className="block text-sm">
            <span className="mb-1 block text-muted">{t("auth.username")}</span>
            <input
              data-testid="login-username"
              name="username"
              autoComplete="username"
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-2 text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent focus:border-accent"
            />
          </label>
          <label className="block text-sm">
            <span className="mb-1 block text-muted">{t("auth.password")}</span>
            <input
              data-testid="login-password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-2 text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent focus:border-accent"
            />
          </label>
          {error && (
            <p
              className="text-sm text-danger"
              data-testid="login-error"
              role="alert"
            >
              {error}
            </p>
          )}
          <Button
            type="submit"
            variant="primary"
            className="w-full"
            disabled={submitting}
            data-testid="login-submit"
          >
            {submitting ? <Spinner /> : t("auth.login")}
          </Button>
        </form>
        {showSsoDivider && (
          <>
            <div className="my-4 flex items-center gap-3 text-xs text-muted">
              <span className="h-px flex-1 bg-hairline" />
              {t("auth.or")}
              <span className="h-px flex-1 bg-hairline" />
            </div>
            <div className="space-y-2">
              {spnegoEnabled && (
                <Button
                  type="button"
                  variant="secondary"
                  className="w-full"
                  data-testid="kerberos-login"
                  onClick={() => startKerberosHandshake()}
                >
                  {t("auth.kerberosButton")}
                </Button>
              )}
              {oidcEnabled && (
                <Button
                  type="button"
                  variant="secondary"
                  className="w-full"
                  data-testid="sso-login"
                  onClick={() => {
                    rememberLoginMethod("oidc");
                    window.location.assign(api.oidcLoginUrl());
                  }}
                >
                  {t("auth.ssoButton")}
                </Button>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
