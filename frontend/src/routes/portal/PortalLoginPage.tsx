import { useEffect, useState, type FormEvent } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { useCustomerAuth } from "@/auth/CustomerAuthContext";
import { logoUrl } from "@/lib/assets";
import { ApiError } from "@/lib/portalApi";
import { customerSecurityApi } from "@/lib/portalSecurityApi";
import { portalSsoLoginUrl, type LoginProvider } from "@/lib/identityApi";
import { ssoErrorKey } from "@/lib/ssoErrors";
import { SsoProviderButtons } from "@/components/auth/SsoProviderButtons";
import { PortalTwoFactorStep } from "@/components/portal/PortalTwoFactorStep";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";

export function PortalLoginPage() {
  const { t } = useTranslation();
  const { login, pending2fa, pendingFactors, isAuthenticated, isLoading } = useCustomerAuth();
  const navigate = useNavigate();
  const search = useSearch({ from: "/portal/login" }) as {
    next?: string;
    sso_error?: string;
    logged_out?: string;
  };
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [providers, setProviders] = useState<LoginProvider[]>([]);
  const [passwordEnabled, setPasswordEnabled] = useState(true);

  useEffect(() => {
    customerSecurityApi
      .methods()
      .then((methods) => {
        setProviders(methods.providers ?? []);
        setPasswordEnabled(methods.password !== false);
      })
      .catch(() => {
        setProviders([]);
        setPasswordEnabled(true);
      });
  }, []);

  // Any non-empty `sso_error` is an error (legacy "1" and unknown codes too).
  const ssoMessageKey = ssoErrorKey(search.sso_error);
  const next = search.next && search.next.startsWith("/portal") ? search.next : "/portal";

  useEffect(() => {
    if (!isLoading && isAuthenticated && !pending2fa) {
      void navigate({ to: next });
    }
  }, [isLoading, isAuthenticated, pending2fa, next, navigate]);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const res = await login(username, password);
      // A pending second factor swaps the form for PortalTwoFactorStep.
      if (!res.pending_2fa) await navigate({ to: next });
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

  const showSsoDivider = providers.length > 0 && passwordEnabled;

  if (pending2fa) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center bg-bg px-4">
        <div className="w-full max-w-sm rounded-xl border border-hairline bg-surface p-8">
          <PortalTwoFactorStep
            factors={pendingFactors ?? { totp: true, passkey: false }}
            onDone={() => void navigate({ to: next })}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-bg px-4">
      <div className="w-full max-w-sm rounded-xl border border-hairline bg-surface p-8">
        <img src={logoUrl} alt="" width={36} height={36} className="mx-auto mb-3" />
        <h1 className="text-center font-display text-2xl font-bold tracking-tight text-ink">
          {t("app.name")}
        </h1>
        <p className="mt-1.5 text-center text-sm text-muted">{t("portal.login.subtitle")}</p>
        {search.logged_out && (
          <p
            className="mt-3 rounded-md border border-hairline bg-surface-subtle px-3 py-2 text-sm text-muted"
            role="status"
            data-testid="portal-logged-out-note"
          >
            {t("auth.loggedOut")}
          </p>
        )}
        {ssoMessageKey && (
          <p
            className="mt-3 rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger"
            role="alert"
            data-testid="portal-sso-error"
          >
            {t(ssoMessageKey)}
          </p>
        )}
        {passwordEnabled && (
        <form
          onSubmit={(e) => void onSubmit(e)}
          className="mt-7 space-y-4"
          data-testid="portal-login-form"
        >
          <label className="block text-sm">
            <span className="mb-1 block text-muted">{t("portal.login.email")}</span>
            <input
              data-testid="portal-login-username"
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
              data-testid="portal-login-password"
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
            <p className="text-sm text-danger" data-testid="portal-login-error" role="alert">
              {error}
            </p>
          )}
          <Button
            type="submit"
            variant="primary"
            className="w-full"
            disabled={submitting}
            data-testid="portal-login-submit"
          >
            {submitting ? <Spinner /> : t("auth.login")}
          </Button>
        </form>
        )}
        {providers.length > 0 && (
          <>
            {showSsoDivider && (
              <div className="my-4 flex items-center gap-3 text-xs text-muted">
                <span className="h-px flex-1 bg-hairline" />
                {t("portal.login.ssoDivider")}
                <span className="h-px flex-1 bg-hairline" />
              </div>
            )}
            <div className={showSsoDivider ? "space-y-2" : "mt-7 space-y-2"}>
              <SsoProviderButtons
                providers={providers}
                hrefFor={(slug) => portalSsoLoginUrl(slug, next)}
              />
            </div>
          </>
        )}
      </div>
    </div>
  );
}
