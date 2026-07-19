import { useCustomerAuth } from "./CustomerAuthContext";
import { Navigate, useRouterState } from "@tanstack/react-router";
import { Spinner } from "@/components/ui/Spinner";
import type { ReactNode } from "react";

export function RequirePortalAuth({ children }: { children: ReactNode }) {
  const { isAuthenticated, isLoading } = useCustomerAuth();
  const pathname = useRouterState({ select: (s) => s.location.pathname });

  if (isLoading) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center gap-2 text-muted">
        <Spinner />
      </div>
    );
  }

  if (!isAuthenticated) {
    const next = encodeURIComponent(pathname || "/portal");
    return <Navigate to="/portal/login" search={{ next }} replace />;
  }

  return children;
}
