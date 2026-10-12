import { useQuery } from "@tanstack/react-query";
import { reportsApi } from "@/lib/reportsApi";
import { useIsKiosk } from "@/dashboard/wallboard/kioskContext";

/** Aggregations whose values may be added up across groups (totals of a cross table). */
export const ADDITIVE_AGGS: ReadonlySet<string> = new Set(["count", "count_if", "sum"]);

/**
 * Aggregation per measure key of one source (`count`, `count_distinct`, `p50`, …)
 * from `GET /reporting/sources`; undefined while loading or unknown (and on a wallboard link). Loaded once.
 */
export function useMeasureAggs(sourceKey: string | undefined): ReadonlyMap<string, string> | undefined {
  // A wallboard link has no login: it never asks an authenticated API (no totals there).
  const kiosk = useIsKiosk();
  const q = useQuery({
    queryKey: ["reports", "sources"],
    queryFn: ({ signal }) => reportsApi.sources(signal),
    staleTime: Infinity,
    retry: false,
    enabled: !kiosk,
  });
  const source = q.data?.find((s) => s.key === sourceKey);
  return source ? new Map(source.measures.map((m) => [m.key, m.agg])) : undefined;
}
