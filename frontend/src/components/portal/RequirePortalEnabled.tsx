import type { ReactNode } from "react";
import { Navigate } from "@tanstack/react-router";
import { Spinner } from "@/components/ui/Spinner";
import { usePortalEnabled } from "@/lib/usePortalEnabled";

/**
 * Keeps /portal* out of reach while the customer portal is switched off.
 * Cosmetic only — the portal API 404s independently (see portal_gate).
 */
export function RequirePortalEnabled({ children }: { children: ReactNode }) {
  const { portalEnabled, isLoading } = usePortalEnabled();

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner />
      </div>
    );
  }

  if (!portalEnabled) {
    return <Navigate to="/login" replace />;
  }

  return <>{children}</>;
}
