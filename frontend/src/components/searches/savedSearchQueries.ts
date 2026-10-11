import { useQuery } from "@tanstack/react-query";
import { savedSearchesApi } from "@/lib/savedSearchesApi";

/** The sidebar refreshes counts once a minute and when the window regains focus, never more often. */
const COUNTS_REFRESH_MS = 60_000;

export const SAVED_SEARCHES_KEY = ["saved-searches"] as const;

/** The saved searches and, for the pinned ones that want it, their match counts. */
export function useSavedSearches() {
  const listQ = useQuery({
    queryKey: [...SAVED_SEARCHES_KEY, "list"],
    queryFn: ({ signal }) => savedSearchesApi.list(signal),
    staleTime: 30_000,
  });
  const wantsCounts = (listQ.data ?? []).some((s) => s.show_in_sidebar && s.show_count);
  const countsQ = useQuery({
    queryKey: [...SAVED_SEARCHES_KEY, "counts"],
    queryFn: ({ signal }) => savedSearchesApi.counts(signal),
    enabled: wantsCounts,
    staleTime: COUNTS_REFRESH_MS,
    refetchInterval: COUNTS_REFRESH_MS,
    refetchOnWindowFocus: true,
  });
  return { listQ, counts: countsQ.data?.counts ?? null };
}
