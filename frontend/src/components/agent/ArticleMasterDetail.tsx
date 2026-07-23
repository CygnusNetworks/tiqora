import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/cn";
import { ArticleSplitView } from "./ArticleSplitView";
import { ArticleConversationView } from "./ArticleConversationView";
import { ArticleComposer } from "./ArticleTimeline";
import { useArticleListState } from "./useArticleListState";
import { useArticleView, type ArticleViewMode } from "./useArticleView";
import type { ReactNode } from "react";

/**
 * Article area orchestrator: owns the shared filter/sort/selection state
 * (`useArticleListState`) and the split/conversation view choice
 * (`useArticleView`), and renders the count/filter/sort bar + view tabs
 * once for whichever view is active. The views themselves
 * (`ArticleSplitView`, `ArticleConversationView`) are presentational and
 * swappable — a further view mode would just add another tab + component
 * here without touching the state hooks.
 */
export function ArticleMasterDetail({
  ticketId,
  onComposingChange,
  noteOpen,
  onNoteOpenChange,
  canNote = true,
  canDelete = false,
  descending,
  onToggleDescending,
}: {
  ticketId: number;
  /** Reported whenever the reply/note composer opens or closes (presence heartbeat). */
  onComposingChange?: (composing: boolean) => void;
  /** Controlled open state for the internal-note composer (header "+ Notiz" / ⋮ menu). */
  noteOpen?: boolean;
  onNoteOpenChange?: (open: boolean) => void;
  /** Whether the agent may reply / add notes (``note`` permission). */
  canNote?: boolean;
  /** Whether the agent may delete internal notes (``rw`` permission). */
  canDelete?: boolean;
  /** Controlled sort direction (shared with the ticket-zoom ⋮ menu's
   * "Sortierung" entry). Uncontrolled + localStorage-backed when omitted. */
  descending?: boolean;
  onToggleDescending?: () => void;
}) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";
  const state = useArticleListState({ ticketId, descending, onToggleDescending });
  const { view, isAuto, setView } = useArticleView(ticketId, state.articles);

  if (state.isLoading) {
    return (
      <div className="flex justify-center py-8">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="space-y-3" data-testid="article-view-root">
      <ViewTabs view={view} isAuto={isAuto} onChange={setView} />

      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs font-medium text-muted">
          {t("ticket.articlesCount", { count: state.articles.length })}
        </p>
        <div className="flex flex-wrap items-center gap-1.5">
          <div className="inline-flex overflow-hidden rounded-md border border-hairline">
            <FilterButton testId="article-filter-all" active={state.filter === "all"} onClick={() => state.setFilter("all")}>
              {t("ticket.filterAll")}
            </FilterButton>
            <FilterButton
              testId="article-filter-email"
              active={state.filter === "email"}
              onClick={() => state.setFilter("email")}
            >
              {t("ticket.filterEmail")}
            </FilterButton>
            <FilterButton
              testId="article-filter-note"
              active={state.filter === "note"}
              onClick={() => state.setFilter("note")}
            >
              {t("ticket.filterNotes")}
            </FilterButton>
          </div>
          <span title={view === "conversation" ? t("ticket.sortDisabledInConversation") : undefined}>
            <Button
              size="sm"
              variant="ghost"
              data-testid="article-sort-toggle"
              disabled={view === "conversation"}
              onClick={state.toggleDescending}
            >
              ⇅ {state.descending ? t("ticket.sortNewestFirst") : t("ticket.sortOldestFirst")}
            </Button>
          </span>
        </div>
      </div>

      {view === "split" ? (
        <ArticleSplitView
          ticketId={ticketId}
          canNote={canNote}
          canDelete={canDelete}
          locale={locale}
          state={state}
        />
      ) : (
        <ArticleConversationView
          ticketId={ticketId}
          articles={state.chronological}
          canNote={canNote}
          canDelete={canDelete}
          locale={locale}
        />
      )}

      {canNote && (
        <ArticleComposer
          ticketId={ticketId}
          articles={state.articles}
          onComposingChange={onComposingChange}
          open={noteOpen}
          onOpenChange={onNoteOpenChange}
        />
      )}
    </div>
  );
}

function ViewTabs({
  view,
  isAuto,
  onChange,
}: {
  view: ArticleViewMode;
  isAuto: boolean;
  onChange: (view: ArticleViewMode) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex items-center gap-4 border-b border-hairline" data-testid="article-view-tabs">
      <ViewTab
        testId="article-view-tab-split"
        active={view === "split"}
        showAutoBadge={isAuto && view === "split"}
        onClick={() => onChange("split")}
      >
        ☰ {t("ticket.viewSplit")}
      </ViewTab>
      <ViewTab
        testId="article-view-tab-conversation"
        active={view === "conversation"}
        showAutoBadge={isAuto && view === "conversation"}
        onClick={() => onChange("conversation")}
      >
        💬 {t("ticket.viewConversation")}
      </ViewTab>
    </div>
  );
}

function ViewTab({
  active,
  showAutoBadge,
  onClick,
  testId,
  children,
}: {
  active: boolean;
  showAutoBadge: boolean;
  onClick: () => void;
  testId: string;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={testId}
      aria-selected={active}
      className={cn(
        "flex items-center gap-1.5 border-b-2 pb-2 text-sm font-medium transition-colors duration-100",
        active ? "border-accent text-ink" : "border-transparent text-muted hover:text-ink",
      )}
    >
      {children}
      {showAutoBadge && (
        <span
          data-testid="article-view-auto-badge"
          title={t("ticket.viewAutoHint")}
          className="rounded-full bg-accent-dim px-1.5 py-0.5 text-[10px] font-semibold text-accent"
        >
          {t("ticket.viewAuto")}
        </span>
      )}
    </button>
  );
}

function FilterButton({
  active,
  onClick,
  testId,
  children,
}: {
  active: boolean;
  onClick: () => void;
  testId: string;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={testId}
      aria-pressed={active}
      className={cn(
        "px-2.5 py-1 text-xs font-medium transition-colors duration-100",
        active ? "bg-accent text-accent-ink" : "bg-surface text-muted hover:bg-surface-subtle",
      )}
    >
      {children}
    </button>
  );
}
