import { useEffect, useState, type FormEvent } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";
import { ApiError } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";

export function LoginPage() {
  const { t } = useTranslation();
  const { login, isAuthenticated, isLoading } = useAuth();
  const navigate = useNavigate();
  const search = useSearch({ from: "/login" }) as { next?: string };
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!isLoading && isAuthenticated) {
      const next =
        search.next && search.next.startsWith("/") ? search.next : "/agent";
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
        search.next && search.next.startsWith("/") ? search.next : "/agent";
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
    <div className="flex min-h-screen flex-col items-center justify-center bg-surface px-4">
      <div className="w-full max-w-sm rounded-xl border border-border bg-surface-elevated p-6 shadow-sm">
        <h1 className="text-center text-xl font-semibold text-accent">
          {t("app.name")}
        </h1>
        <p className="mt-1 text-center text-sm text-muted">{t("auth.signIn")}</p>
        <form
          onSubmit={(e) => void onSubmit(e)}
          className="mt-6 space-y-4"
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
              className="w-full rounded-md border border-border bg-surface px-3 py-2 text-ink focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
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
              className="w-full rounded-md border border-border bg-surface px-3 py-2 text-ink focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
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
      </div>
    </div>
  );
}
