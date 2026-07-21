import { useEffect, useState, type FormEvent } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";
import { api, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";

function browserSupportsWebAuthn(): boolean {
  return typeof window !== "undefined" && typeof window.PublicKeyCredential !== "undefined";
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

export function LoginPage() {
  const { t } = useTranslation();
  const {
    login,
    verifyTotp,
    verifyPasskey,
    completeEnroll2fa,
    completeEnrollPasskey,
    pending2fa,
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
  }, [isLoading, isAuthenticated, mustEnroll2fa, pending2fa, search.next, navigate]);

  // Auto-start TOTP enrollment when forced into must-enroll mode (unless the
  // agent is mid passkey registration as the alternative path).
  useEffect(() => {
    if (!mustEnroll2fa || enrollSecret || enrollStarting || passkeyEnrolling) return;
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

  const onVerifyTotp = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await verifyTotp(totpCode);
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

  const onVerifyPasskey = async () => {
    setError(null);
    setSubmitting(true);
    try {
      await verifyPasskey();
      await goNext();
    } catch (err) {
      if (err instanceof ApiError && (err.status === 401 || err.status === 400)) {
        setError(t("auth.passkeyInvalid"));
      } else {
        setError(t("auth.passkeyFailed"));
      }
    } finally {
      setSubmitting(false);
    }
  };

  const onConfirmEnroll = async (e: FormEvent) => {
    e.preventDefault();
    setEnrollError(null);
    setSubmitting(true);
    try {
      await completeEnroll2fa(enrollCode);
      await goNext();
    } catch {
      setEnrollError(t("auth.mustEnroll.confirmError"));
    } finally {
      setSubmitting(false);
    }
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

  const showPasskeyLogin =
    webauthnEnabled && browserSupportsWebAuthn();
  const showPasskeyEnroll =
    webauthnEnabled && browserSupportsWebAuthn();

  if (mustEnroll2fa) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center bg-bg px-4">
        <div className="w-full max-w-sm rounded-xl border border-hairline bg-surface p-8">
          <h1 className="text-center font-display text-2xl font-bold tracking-tight text-ink">
            {t("auth.mustEnroll.title")}
          </h1>
          <p className="mt-1.5 text-center text-sm text-muted" data-testid="must-enroll-hint">
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
                <code data-testid="must-enroll-secret" className="font-mono text-ink">
                  {enrollSecret}
                </code>
              </p>
              <form
                onSubmit={(e) => void onConfirmEnroll(e)}
                className="space-y-3"
                data-testid="must-enroll-form"
              >
                <label className="block text-sm">
                  <span className="mb-1 block text-muted">{t("security.confirmCode")}</span>
                  <input
                    data-testid="must-enroll-code"
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    required
                    value={enrollCode}
                    onChange={(e) => setEnrollCode(e.target.value)}
                    className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-2 text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent focus:border-accent"
                  />
                </label>
                {enrollError && (
                  <p
                    className="text-sm text-danger"
                    role="alert"
                    data-testid="must-enroll-error"
                  >
                    {enrollError}
                  </p>
                )}
                <Button
                  type="submit"
                  variant="primary"
                  className="w-full"
                  disabled={submitting}
                  data-testid="must-enroll-submit"
                >
                  {submitting && !passkeyEnrolling ? <Spinner /> : t("security.confirmButton")}
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
            <p className="mt-4 text-sm text-danger" role="alert" data-testid="must-enroll-error">
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
            {t("auth.totpTitle")}
          </h1>
          <p className="mt-1.5 text-center text-sm text-muted">{t("auth.totpHint")}</p>
          <form
            onSubmit={(e) => void onVerifyTotp(e)}
            className="mt-7 space-y-4"
            data-testid="totp-form"
          >
            <label className="block text-sm">
              <span className="mb-1 block text-muted">{t("auth.totpCode")}</span>
              <input
                data-testid="totp-code"
                name="code"
                inputMode="numeric"
                autoComplete="one-time-code"
                required
                value={totpCode}
                onChange={(e) => setTotpCode(e.target.value)}
                className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-2 text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent focus:border-accent"
              />
            </label>
            {error && (
              <p className="text-sm text-danger" data-testid="totp-error" role="alert">
                {error}
              </p>
            )}
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

          {showPasskeyLogin && (
            <>
              <div className="my-4 flex items-center gap-3 text-xs text-muted">
                <span className="h-px flex-1 bg-hairline" />
                {t("auth.or")}
                <span className="h-px flex-1 bg-hairline" />
              </div>
              <Button
                type="button"
                variant="secondary"
                className="w-full"
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
  const ssoError = search.sso_error === "1" || search.sso_error === "true";

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-bg px-4">
      <div className="w-full max-w-sm rounded-xl border border-hairline bg-surface p-8">
        <img src="/logo.svg" alt="" width={36} height={36} className="mx-auto mb-3" />
        <h1 className="text-center font-display text-2xl font-bold tracking-tight text-ink">
          {t("app.name")}
        </h1>
        <p className="mt-1.5 text-center text-sm text-muted">{t("auth.signIn")}</p>
        {ssoError && (
          <p
            className="mt-3 rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger"
            role="alert"
            data-testid="sso-error"
          >
            {t("auth.ssoFailed")}
          </p>
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
            <p className="text-sm text-danger" data-testid="login-error" role="alert">
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
                  onClick={() => {
                    window.location.assign(api.spnegoLoginUrl());
                  }}
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
