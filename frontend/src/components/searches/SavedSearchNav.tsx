import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { Popover } from "@/components/ui/Popover";
import { usePopoverClose } from "@/components/ui/popoverContext";
import { Switch } from "@/components/ui/Switch";
import { cn } from "@/lib/cn";
import { savedSearchesApi, type SavedSearch } from "@/lib/savedSearchesApi";
import { SAVED_SEARCHES_KEY, useSavedSearches } from "./savedSearchQueries";

const rowBase =
  "group flex items-center gap-2.5 rounded-lg px-2.5 py-[7px] text-[13.5px] transition-colors duration-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";

function SearchRow({
  search,
  count,
  onNavigate,
}: {
  search: SavedSearch;
  /** `undefined`: counts not loaded yet; `null`: this count is not available. */
  count: number | null | undefined;
  onNavigate?: () => void;
}) {
  const { t } = useTranslation();
  const unavailable = search.show_count && count === null;
  return (
    <Link
      to="/agent/searches/$searchId"
      params={{ searchId: String(search.id) }}
      search={{}}
      onClick={onNavigate}
      data-testid={`sidebar-search-${search.id}`}
      title={unavailable ? t("savedSearches.countUnavailable") : undefined}
      className={cn(rowBase, "text-ink/80 hover:bg-surface-subtle")}
      activeProps={{
        className: cn(rowBase, "bg-accent-dim font-medium text-ink shadow-[inset_2px_0_0_var(--color-accent)]"),
        "aria-current": "page",
      }}
    >
      <span className="min-w-0 flex-1 truncate">{search.name}</span>
      {search.show_count && typeof count === "number" && (
        <span
          className="shrink-0 rounded-full px-1.5 py-0.5 font-mono text-[11px] tabular-nums text-muted"
          data-testid={`sidebar-search-${search.id}-count`}
          aria-label={t("savedSearches.countLabel", { count })}
        >
          {count}
        </span>
      )}
    </Link>
  );
}

/** Every search the person can see, each with a switch for "show in the sidebar". */
function AllSearches({ searches, onNavigate }: { searches: SavedSearch[]; onNavigate?: () => void }) {
  const { t } = useTranslation();
  const close = usePopoverClose();
  const queryClient = useQueryClient();
  const pin = useMutation({
    mutationFn: ({ search, on }: { search: SavedSearch; on: boolean }) =>
      savedSearchesApi.setPrefs(search.id, { show_in_sidebar: on, show_count: search.show_count }),
    onSettled: () => queryClient.invalidateQueries({ queryKey: SAVED_SEARCHES_KEY }),
  });
  if (searches.length === 0) {
    return <p className="px-1 py-2 text-xs text-muted">{t("savedSearches.empty")}</p>;
  }
  return (
    <ul className="space-y-0.5" data-testid="sidebar-searches-all-list">
      {searches.map((s) => (
        <li key={s.id} className="flex items-center gap-2 rounded-md px-1.5 py-1">
          <Link
            to="/agent/searches/$searchId"
            params={{ searchId: String(s.id) }}
            search={{}}
            onClick={() => {
              close();
              onNavigate?.();
            }}
            className="min-w-0 flex-1 truncate text-[13px] text-ink hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
            data-testid={`sidebar-searches-all-link-${s.id}`}
          >
            {s.name}
          </Link>
          <Switch
            checked={s.show_in_sidebar}
            onChange={(on) => pin.mutate({ search: s, on })}
            aria-label={t("savedSearches.pinAria", { name: s.name })}
            testId={`sidebar-searches-pin-${s.id}`}
          />
        </li>
      ))}
    </ul>
  );
}

/** Body of the sidebar's "Gespeicherte Suchen" group: pinned searches with counts and the list of all. */
export function SavedSearchNav({ onNavigate }: { onNavigate?: () => void }) {
  const { t } = useTranslation();
  const { listQ, counts } = useSavedSearches();
  const all = listQ.data ?? [];
  const pinned = all.filter((s) => s.show_in_sidebar).sort((a, b) => a.position - b.position || a.id - b.id);
  return (
    <div className="space-y-0.5" data-testid="sidebar-search-list">
      {pinned.map((s) => (
        <SearchRow
          key={s.id}
          search={s}
          count={counts === null ? undefined : (counts[String(s.id)] ?? null)}
          onNavigate={onNavigate}
        />
      ))}
      {listQ.isError && (
        <p className="px-2.5 py-2 text-[11.5px] text-muted" role="status">
          {t("savedSearches.loadError")}
        </p>
      )}
      {listQ.isSuccess && pinned.length === 0 && (
        <p className="px-2.5 py-2 text-[11.5px] text-muted" data-testid="sidebar-search-empty">
          {t("savedSearches.empty")}
        </p>
      )}
      {listQ.isSuccess && (
        <Popover
          align="left"
          label={t("savedSearches.allTitle")}
          panelTestId="sidebar-searches-all-panel"
          panelClassName="w-80"
          trigger={({ ref, toggleProps }) => (
            <button
              ref={ref}
              type="button"
              {...toggleProps}
              data-testid="sidebar-searches-all"
              className="w-full rounded-lg px-2.5 py-1 text-left text-[11.5px] text-muted hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
            >
              {t("savedSearches.all")}
            </button>
          )}
        >
          <AllSearches searches={all} onNavigate={onNavigate} />
        </Popover>
      )}
    </div>
  );
}
