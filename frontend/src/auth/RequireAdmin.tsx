import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "@tanstack/react-router";
import { RequireAuth } from "./RequireAuth";
import { useAuth } from "./AuthContext";
import { Spinner } from "@/components/ui/Spinner";

/**
 * Admin-capability guard. Uses ``UserMe.is_admin`` from ``GET /api/v1/auth/me``
 * (``PermissionEngine.is_admin`` — rw on the group named ``admin``). No extra
 * probe request; session/auth loading is owned by RequireAuth + AuthContext.
 */
function AdminCapabilityCheck({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const { user, isLoading } = useAuth();

  if (isLoading) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center gap-2 text-muted">
        <Spinner />
      </div>
    );
  }

  if (!user?.is_admin) {
    return (
      <div
        className="mx-auto max-w-lg space-y-3 px-4 py-16 text-center"
        data-testid="admin-access-denied"
      >
        <h1 className="font-display text-2xl font-semibold text-ink">
          {t("admin.accessDenied.title")}
        </h1>
        <p className="text-muted">{t("admin.accessDenied.body")}</p>
        <Link to="/agent" className="text-sm text-accent hover:underline">
          {t("nav.dashboard")}
        </Link>
      </div>
    );
  }

  return children;
}

/** Composes RequireAuth (agent session) with the is_admin flag from /me. */
export function RequireAdmin({ children }: { children: ReactNode }) {
  return (
    <RequireAuth>
      <AdminCapabilityCheck>{children}</AdminCapabilityCheck>
    </RequireAuth>
  );
}
