import { useQuery } from "@tanstack/react-query";
import { portalCmdbApi } from "@/lib/portalCmdbApi";

/** Max devices/systems a customer can attach to a new ticket (backend `max_length=10`). */
export const PORTAL_CI_PICKER_MAX = 10;

/**
 * The customer's visible devices/systems. A 404 (CMDB off / Znuny mode), any error or an empty list
 * all mean "feature absent": `available` is true only for a 200 with `total > 0`.
 */
export function usePortalCis() {
  const q = useQuery({
    queryKey: ["portal", "cmdb", "picker"],
    queryFn: () => portalCmdbApi.list({ limit: 100 }),
    retry: false,
    staleTime: 60_000,
  });
  const items = q.data?.items ?? [];
  return { items, available: (q.data?.total ?? 0) > 0 && items.length > 0, isLoading: q.isLoading };
}
