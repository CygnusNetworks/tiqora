import { useEffect, useState, type FormEvent } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { useCustomerAuth } from "@/auth/CustomerAuthContext";
import { ApiError } from "@/lib/portalApi";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";

export function PortalLoginPage() {
  const { t } = useTranslation();
  const { login, isAuthenticated, isLoading } = useCustomerAuth();
  const navigate = useNavigate();
  const search = useSearch({ from: "/portal/login" }) as { next?: string };
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!isLoading && isAuthenticated) {
      const next =
        search.next && search.next.startsWith("/portal") ? search.next : "/portal";
      void navigate({ to: next });
    }
  }, [isLoading, isAuthenticated, search.next, navigate]);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(username, password);
      const next =
        search.next && search.next.startsWith("/portal") ? search.next : "/portal";
      await navigate({ to: next });
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

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-bg px-4">
      <div className="w-full max-w-sm rounded-xl border border-hairline bg-surface p-8">
        <img src="/logo.svg" alt="" width={36} height={36} className="mx-auto mb-3" />
        <h1 className="text-center font-display text-2xl font-bold tracking-tight text-ink">
          {t("app.name")}
        </h1>
        <p className="mt-1.5 text-center text-sm text-muted">{t("portal.login.subtitle")}</p>
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
      </div>
    </div>
  );
}
