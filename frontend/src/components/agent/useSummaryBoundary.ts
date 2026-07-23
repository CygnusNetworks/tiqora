import { useQuery } from "@tanstack/react-query";
import type { ArticleListItem } from "@/lib/api";
import { ticketAiApi } from "@/lib/ticketAiApi";

/**
 * Where the current AI summary ends within an article list: `boundaryId` is
 * the newest article the summary covers (marker renders adjacent to it),
 * `createdAt` the summary's generation timestamp.
 *
 * Shares the `["tickets", id, "ai"]` query key with `AiPanel`, so in the
 * zoom page this never issues a second request.
 */
export function useSummaryBoundary(ticketId: number, articles: ArticleListItem[]) {
  const stateQ = useQuery({
    queryKey: ["tickets", ticketId, "ai"],
    queryFn: ({ signal }) => ticketAiApi.getState(ticketId, signal),
  });
  const state = stateQ.data;
  const upto = state?.summary_available ? state.last_summary_upto_article_id : null;
  if (upto == null || !state?.summary_body) {
    return { boundaryId: null, createdAt: null };
  }
  // Only meaningful when a covered article exists in the (possibly
  // filtered) list the caller renders.
  const covered = articles.filter((a) => a.id <= upto);
  if (covered.length === 0) return { boundaryId: null, createdAt: null };
  const boundaryId = covered.reduce((max, a) => (a.id > max ? a.id : max), covered[0].id);
  return { boundaryId, createdAt: state.summary_created_at };
}
