import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type ArticleListItem } from "@/lib/api";
import { articleSortKey } from "@/lib/article";
import { CHANNEL_EMAIL, CHANNEL_INTERNAL } from "@/lib/articleChannel";

export type ArticleFilter = "all" | "email" | "note";

const SORT_STORAGE_KEY = "tiqora.articleList.sortDescending";
const FILTER_STORAGE_KEY = "tiqora.articleList.filter";

function readStoredFilter(): ArticleFilter {
  if (typeof window === "undefined") return "all";
  try {
    const v = window.localStorage.getItem(FILTER_STORAGE_KEY);
    return v === "email" || v === "note" ? v : "all";
  } catch {
    return "all";
  }
}

function readStoredDescending(): boolean {
  if (typeof window === "undefined") return true;
  try {
    const v = window.localStorage.getItem(SORT_STORAGE_KEY);
    return v === null ? true : v === "true";
  } catch {
    return true;
  }
}

/**
 * Article data + filter/sort/selection state, shared between the split
 * (master-detail) and conversation (chat-bubble) views so switching tabs
 * keeps the same filter/sort choice and doesn't re-fetch. Pulled out of the
 * split view specifically so a future second view can reuse it without
 * depending on that view's render component.
 */
export function useArticleListState({
  ticketId,
  descending: descendingProp,
  onToggleDescending,
}: {
  ticketId: number;
  /** Controlled sort direction (shared with the ticket-zoom ⋮ menu's
   * "Sortierung" entry). Uncontrolled + localStorage-backed when omitted. */
  descending?: boolean;
  onToggleDescending?: () => void;
}) {
  const [filter, setFilter] = useState<ArticleFilter>(readStoredFilter);
  const [descendingLocal, setDescendingLocal] = useState<boolean>(readStoredDescending);
  const sortControlled = descendingProp !== undefined;
  const descending = sortControlled ? descendingProp : descendingLocal;
  const toggleDescending = () => {
    if (sortControlled) onToggleDescending?.();
    else setDescendingLocal((d) => !d);
  };
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const articlesQ = useQuery({
    queryKey: ["tickets", ticketId, "articles"],
    queryFn: () => api.listArticles(ticketId),
  });
  // Memoized so `filtered` below (which depends on this array) doesn't
  // re-derive on every render while the query is still loading.
  const articles = useMemo(() => articlesQ.data ?? [], [articlesQ.data]);

  useEffect(() => {
    try {
      window.localStorage.setItem(FILTER_STORAGE_KEY, filter);
    } catch {
      // best-effort persistence only
    }
  }, [filter]);
  useEffect(() => {
    try {
      window.localStorage.setItem(SORT_STORAGE_KEY, String(descending));
    } catch {
      // best-effort persistence only
    }
  }, [descending]);

  const filtered = useMemo(
    () =>
      articles.filter((a) => {
        if (filter === "email") return a.communication_channel_id === CHANNEL_EMAIL;
        if (filter === "note") return a.communication_channel_id === CHANNEL_INTERNAL;
        return true;
      }),
    [articles, filter],
  );

  // Split view's order — respects `descending`.
  const sorted = useMemo(() => {
    const list = [...filtered].sort((a, b) => articleSortKey(a) - articleSortKey(b));
    return descending ? list.reverse() : list;
  }, [filtered, descending]);

  // Conversation view always renders oldest→newest, independent of the
  // split view's sort toggle.
  const chronological = useMemo(
    () => [...filtered].sort((a, b) => articleSortKey(a) - articleSortKey(b)),
    [filtered],
  );

  // Default selection = newest article in the current filter; also
  // reselect whenever the active selection drops out of the filtered set
  // (filter switch, sort toggle never removes items so this mostly fires
  // on filter changes).
  useEffect(() => {
    if (sorted.length === 0) {
      setSelectedId(null);
      return;
    }
    if (selectedId != null && sorted.some((a) => a.id === selectedId)) return;
    const newest = [...sorted].sort((a, b) => articleSortKey(b) - articleSortKey(a))[0];
    setSelectedId(newest.id);
    // Only re-derive when the candidate set changes, not on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sorted]);

  const selected = sorted.find((a) => a.id === selectedId) ?? null;

  const onListKeyDown = (e: React.KeyboardEvent) => {
    if (sorted.length === 0) return;
    const idx = sorted.findIndex((a) => a.id === selectedId);
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelectedId((sorted[Math.min(idx + 1, sorted.length - 1)] ?? sorted[0]).id);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedId((sorted[Math.max(idx - 1, 0)] ?? sorted[0]).id);
    }
  };

  return {
    isLoading: articlesQ.isLoading,
    articles,
    filtered,
    sorted,
    chronological,
    selectedId,
    setSelectedId,
    selected,
    filter,
    setFilter,
    descending,
    toggleDescending,
    onListKeyDown,
  };
}

export type ArticleListState = ReturnType<typeof useArticleListState>;

// Re-exported so consumers of the hook don't also need to import
// `ArticleListItem` from `@/lib/api` just to type intermediate values.
export type { ArticleListItem };
