import { Navigate } from "@tanstack/react-router";
import { useAuth } from "@/auth/AuthContext";
import { Spinner } from "@/components/ui/Spinner";
import { useTranslation } from "react-i18next";
import { Link } from "@tanstack/react-router";

export function HomeRedirect() {
  const { isAuthenticated, isLoading } = useAuth();
  const { t } = useTranslation();

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner />
      </div>
    );
  }

  if (isAuthenticated) {
    return <Navigate to="/agent" replace />;
  }

  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-6 bg-surface px-4">
      <div className="text-center">
        <h1 className="text-3xl font-semibold text-accent">{t("app.name")}</h1>
        <p className="mt-2 text-muted">{t("app.tagline")}</p>
      </div>
      <div className="flex flex-wrap justify-center gap-3">
        <Link
          to="/login"
          className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white"
        >
          {t("auth.login")}
        </Link>
        <Link
          to="/portal"
          className="rounded-md border border-border px-4 py-2 text-sm text-ink"
        >
          {t("nav.portal")}
        </Link>
      </div>
    </div>
  );
}
