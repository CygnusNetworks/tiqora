import { useQuery } from "@tanstack/react-query";
import { portalChangeApi, portalChangeKeys } from "@/lib/portalChangeApi";

/**
 * The portal's "Wartungsfenster" entry shows only when the schedule answers 200 (it is a 404 while
 * ITSM change management or `portal_schedule_enabled` is off); any error means "feature absent".
 */
export function usePortalChangeScheduleAvailable() {
  const q = useQuery({
    queryKey: portalChangeKeys.available(),
    queryFn: ({ signal }) => {
      const start = new Date();
      return portalChangeApi.list(start.toISOString(), new Date(start.getTime() + 86_400_000).toISOString(), signal);
    },
    retry: false,
    staleTime: 60_000,
  });
  return { available: q.isSuccess, isLoading: q.isLoading };
}
