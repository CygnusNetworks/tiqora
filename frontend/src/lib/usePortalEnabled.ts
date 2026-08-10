import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

/** Shared cache key — the login page reads the same discovery response. */
export const PORTAL_ENABLED_KEY = ["auth", "methods"] as const;

/**
 * Whether the customer portal is switched on.
 *
 * Fails open: on a failed discovery call the portal counts as enabled. The
 * backend 404-gate is what actually protects the portal, so a network blip
 * must not strand customers on the agent login.
 */
export function usePortalEnabled(): {
  portalEnabled: boolean;
  isLoading: boolean;
} {
  const q = useQuery({
    queryKey: PORTAL_ENABLED_KEY,
    queryFn: ({ signal }) => api.authMethods(signal),
    staleTime: 5 * 60 * 1000,
    retry: false,
  });

  return {
    portalEnabled: q.data?.portal_enabled ?? true,
    isLoading: q.isLoading,
  };
}
