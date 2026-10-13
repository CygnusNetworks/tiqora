import { useQuery } from "@tanstack/react-query";
import { portalCmdbApi, type PortalCi } from "@/lib/portalCmdbApi";

/** Max devices/systems a customer can attach to a new ticket (backend `max_length=10`). */
export const PORTAL_CI_PICKER_MAX = 10;

/** Size of the picker's first page. */
const PICKER_PAGE = 100;

/**
 * The customer's visible devices/systems. A 404 (CMDB off / Znuny mode), any error or an empty list
 * all mean "feature absent": `available` is true only for a 200 with `total > 0`.
 *
 * `preselect` (the `?ci=` of "Ticket zu diesem System erstellen") is loaded on its own when it is
 * not on the first page, so a customer with more devices than the page still gets it attached.
 */
export function usePortalCis(preselect?: number) {
  const q = useQuery({
    queryKey: ["portal", "cmdb", "picker"],
    queryFn: () => portalCmdbApi.list({ limit: PICKER_PAGE }),
    retry: false,
    staleTime: 60_000,
  });
  const listed = q.data?.items ?? [];
  const available = (q.data?.total ?? 0) > 0 && listed.length > 0;
  const missing = preselect !== undefined && available && !listed.some((c) => c.id === preselect);
  const extraQ = useQuery({
    queryKey: ["portal", "cmdb", "picker", "ci", preselect],
    queryFn: () => portalCmdbApi.get(preselect as number),
    enabled: missing,
    retry: false,
    staleTime: 60_000,
  });
  let items: PortalCi[] = listed;
  if (missing && extraQ.data) {
    const { rendered: _rendered, ...one } = extraQ.data;
    items = [one, ...listed];
  }
  return { items, available, isLoading: q.isLoading || (missing && extraQ.isLoading) };
}
