import { useQuery } from "@tanstack/react-query";
import { reportsApi } from "@/lib/reportsApi";

/** Aggregations whose values may be added up across groups (totals of a cross table). */
export const ADDITIVE_AGGS: ReadonlySet<string> = new Set(["count", "count_if", "sum"]);

/**
 * Aggregation per measure key of one source (`count`, `count_distinct`, `p50`, …)
 * from `GET /reporting/sources`; undefined while loading or unknown. Loaded once.
 */
export function useMeasureAggs(sourceKey: string | undefined): ReadonlyMap<string, string> | undefined {
  const q = useQuery({
    queryKey: ["reports", "sources"],
    queryFn: ({ signal }) => reportsApi.sources(signal),
    staleTime: Infinity,
    retry: false,
  });
  const source = q.data?.find((s) => s.key === sourceKey);
  return source ? new Map(source.measures.map((m) => [m.key, m.agg])) : undefined;
}
