import { useAuth } from "./AuthContext";
import { Navigate, useRouterState } from "@tanstack/react-router";
import { Spinner } from "@/components/ui/Spinner";
import type { ReactNode } from "react";

export function RequireAuth({ children }: { children: ReactNode }) {
  const { isAuthenticated, isLoading } = useAuth();
  const pathname = useRouterState({ select: (s) => s.location.pathname });

  if (isLoading) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center gap-2 text-muted">
        <Spinner />
      </div>
    );
  }

  if (!isAuthenticated) {
    const next = encodeURIComponent(pathname || "/agent");
    return <Navigate to="/login" search={{ next }} replace />;
  }

  return children;
}
