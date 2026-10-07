import { useAuth } from "./AuthContext";
import { Navigate, useRouterState } from "@tanstack/react-router";
import { Spinner } from "@/components/ui/Spinner";
import type { ReactNode } from "react";

export function RequireAuth({ children }: { children: ReactNode }) {
  const { isAuthenticated, isLoading } = useAuth();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const searchStr = useRouterState({ select: (s) => s.location.searchStr });

  if (isLoading) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center gap-2 text-muted">
        <Spinner />
      </div>
    );
  }

  if (!isAuthenticated) {
    // The router flips the location to /login before this guard unmounts. In
    // that window we still render, and navigating again would stack a new
    // `next=/login?next=…` on every pass — an endless redirect loop (endless
    // spinner, memory growth) instead of the login form.
    if (pathname === "/login" || pathname.startsWith("/login/")) return null;
    // Path + query (an /agent/dial link from a mail needs its ?number=&ticket=).
    // Not pre-encoded: the router encodes `search` itself, and a second
    // encoding made LoginPage see "%2Fagent..." and drop the target.
    const next = `${pathname || "/agent"}${searchStr ?? ""}`;
    return <Navigate to="/login" search={{ next }} replace />;
  }

  return children;
}
