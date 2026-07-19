import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { RequireAuth } from "./RequireAuth";
import { api, ApiError } from "@/lib/api";
import { Spinner } from "@/components/ui/Spinner";

/**
 * Admin-capability guard. UserMe carries no explicit "is_admin"/capabilities
 * field, so we probe a lightweight admin endpoint on mount: a 403 renders an
 * "access denied" page, while a 401 is already handled globally by the
 * ApiClient's onUnauthorized redirect (see lib/api.ts) before this component
 * would even see it reject.
 */
function AdminCapabilityCheck({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const probe = useQuery({
    queryKey: ["admin", "capability-probe"],
    queryFn: () => api.adminGroups.list(),
    retry: false,
  });

  if (probe.isLoading) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center gap-2 text-muted">
        <Spinner />
      </div>
    );
  }

  if (probe.isError) {
    const forbidden = probe.error instanceof ApiError && probe.error.isForbidden;
    return (
      <div
        className="mx-auto max-w-lg space-y-3 px-4 py-16 text-center"
        data-testid="admin-access-denied"
      >
        <h1 className="font-display text-2xl font-semibold text-ink">
          {t("admin.accessDenied.title")}
        </h1>
        <p className="text-muted">
          {forbidden ? t("admin.accessDenied.body") : t("admin.accessDenied.error")}
        </p>
        <Link to="/agent" className="text-sm text-accent hover:underline">
          {t("nav.dashboard")}
        </Link>
      </div>
    );
  }

  return children;
}

/** Composes RequireAuth (agent session) with the admin-capability probe. */
export function RequireAdmin({ children }: { children: ReactNode }) {
  return (
    <RequireAuth>
      <AdminCapabilityCheck>{children}</AdminCapabilityCheck>
    </RequireAuth>
  );
}
