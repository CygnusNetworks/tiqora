import { useQuery } from "@tanstack/react-query";
import { portalApprovalsApi, portalApprovalsKeys } from "@/lib/portalApprovalsApi";

/**
 * The portal's "Freigaben" entry shows only when the list endpoint answers 200 (it is a 404 while
 * customer approvers or the portal approvals are switched off); any error means "feature absent".
 */
export function usePortalApprovalsAvailable() {
  const q = useQuery({
    queryKey: portalApprovalsKeys.available(),
    queryFn: ({ signal }) => portalApprovalsApi.list({ limit: 1 }, signal),
    retry: false,
    staleTime: 60_000,
  });
  return { available: q.isSuccess, isLoading: q.isLoading };
}
