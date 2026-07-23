import { Fragment, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, type ArticleListItem } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { decodeEntities, stripHtml } from "@/lib/html";
import { groupByDay } from "@/lib/article";
import {
  avatarTone,
  channelIcon,
  emailFromAddress,
  initialsFor,
  isInternalNote,
  senderDisplayName,
} from "@/lib/articleChannel";
import { cn } from "@/lib/cn";
import { Avatar } from "@/components/ui/Avatar";
import { ArticleBodyRenderer } from "./ArticleBodyRenderer";
import { ArticleQuickActions } from "./ArticleQuickActions";
import { SummaryMarker } from "./SummaryMarker";
import { useSummaryBoundary } from "./useSummaryBoundary";
import { useDeleteArticleNote } from "./useDeleteArticleNote";

/** Collapsed-preview length in characters — a rough proxy for "~6 lines" of
 * chat-bubble text without depending on CSS line-clamp (which doesn't work
 * against the sandboxed HTML iframe `ArticleBodyRenderer` uses for rich
 * bodies — see the collapsed/expanded split in `Bubble` below). */
const PREVIEW_CHARS = 400;

function bubbleSide(senderType: string | null | undefined): "left" | "right" {
  const s = (senderType || "").toLowerCase();
  return s === "agent" ? "right" : "left";
}

/**
 * Article list "Variante B — Unterhaltung": chat-bubble layout for
 * WhatsApp/SMS/Chat-dominant tickets. Reuses the same filtered/chronological
 * article set the split view derives from `useArticleListState` — this
 * component only owns per-bubble expand state.
 */
export function ArticleConversationView({
  ticketId,
  articles,
  canNote,
  canDelete = false,
  locale,
}: {
  ticketId: number;
  /** Already filtered, chronological (oldest→newest) — see
   * `useArticleListState().chronological`. */
  articles: ArticleListItem[];
  canNote: boolean;
  /** Whether the agent may delete internal notes (``rw`` permission). */
  canDelete?: boolean;
  locale: string;
}) {
  const { t } = useTranslation();
  const bottomRef = useRef<HTMLDivElement>(null);
  const { boundaryId, createdAt } = useSummaryBoundary(ticketId, articles);

  // Scroll to the newest message whenever this view mounts (tab switch) or
  // the ticket changes. jsdom (tests) doesn't implement scrollIntoView.
  useEffect(() => {
    bottomRef.current?.scrollIntoView?.({ block: "end" });
  }, [ticketId]);

  const groups = groupByDay(articles, locale);

  return (
    <div
      className="max-h-[60vh] space-y-4 overflow-y-auto rounded-lg border border-hairline bg-surface p-3"
      data-testid="article-conversation"
    >
      {articles.length === 0 ? (
        <p className="text-sm text-muted">{t("ticket.noArticles")}</p>
      ) : (
        groups.map((g) => (
          <section key={g.day} className="space-y-2">
            <div className="flex items-center justify-center">
              <span className="rounded-full bg-surface-subtle px-2 py-0.5 text-[10px] uppercase tracking-wide text-muted">
                {g.day}
              </span>
            </div>
            {g.items.map((a) => (
              <Fragment key={a.id}>
                {isInternalNote(a) ? (
                  <NotePill
                    ticketId={ticketId}
                    article={a}
                    canDelete={canDelete}
                    locale={locale}
                  />
                ) : (
                  <Bubble
                    ticketId={ticketId}
                    article={a}
                    canNote={canNote}
                    canDelete={canDelete}
                    locale={locale}
                    side={bubbleSide(a.sender_type)}
                  />
                )}
                {/* Chronological view → marker goes below the newest
                    summarized article. */}
                {a.id === boundaryId && <SummaryMarker createdAt={createdAt} locale={locale} />}
              </Fragment>
            ))}
          </section>
        ))
      )}
      <div ref={bottomRef} />
    </div>
  );
}

function useArticleBody(ticketId: number, articleId: number) {
  return useQuery({
    queryKey: ["tickets", ticketId, "articles", articleId, "body"],
    queryFn: () => api.getArticleBody(ticketId, articleId),
  });
}

function Bubble({
  ticketId,
  article,
  canNote,
  canDelete,
  locale,
  side,
}: {
  ticketId: number;
  article: ArticleListItem;
  canNote: boolean;
  canDelete: boolean;
  locale: string;
  side: "left" | "right";
}) {
  const { t } = useTranslation();
  const senderName = senderDisplayName(article.from_address) || t("ticket.unknownSender");
  const isSystem = (article.sender_type || "").toLowerCase() === "system";
  // Side alone is easy to lose while scrolling — pair it with a clear hue:
  // agent = accent (cobalt), customer = green, system = neutral. Border and
  // sender-name color repeat the hue so it also works for short bubbles.
  const tone = isSystem
    ? "border border-hairline bg-surface-subtle"
    : side === "right"
      ? "border border-accent/35 bg-accent/15 rounded-br-md"
      : "border border-green/35 bg-green/10 rounded-bl-md";
  const nameTone = isSystem ? "text-muted" : side === "right" ? "text-accent" : "text-green";

  return (
    <div
      className={cn("group flex", side === "right" ? "justify-end" : "justify-start")}
      data-testid={`conversation-bubble-${article.id}`}
      data-side={side}
    >
      <div className={cn("flex max-w-[76%] items-start gap-2", side === "right" && "flex-row-reverse")}>
        <Avatar
          initials={initialsFor(article)}
          email={emailFromAddress(article.from_address)}
          tone={avatarTone(article.sender_type)}
          size={24}
          className="mt-0.5"
        />
        <div className="relative min-w-0">
          <div className={cn("space-y-1 rounded-2xl px-3 py-2", tone)}>
            <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted">
              <span className={cn("font-semibold", nameTone)}>{senderName}</span>
              <span aria-hidden>{channelIcon(article.communication_channel_id)}</span>
              <span className="font-mono tabular-nums">{formatDateTime(article.create_time, locale)}</span>
              <span
                className={cn(
                  "h-1.5 w-1.5 shrink-0 rounded-full",
                  article.is_visible_for_customer ? "bg-green" : "bg-escalation",
                )}
                title={
                  article.is_visible_for_customer
                    ? t("ticket.articleVisibleTooltip")
                    : t("ticket.articleInternalTooltip")
                }
              />
            </div>
            <BubbleBody ticketId={ticketId} article={article} />
          </div>
          {/* Hover/focus action island — same handlers as the split view's
              reading pane, just icon-only to fit a bubble. */}
          <div className="pointer-events-none absolute -top-3 right-1 opacity-0 transition-opacity duration-100 group-hover:opacity-100 group-focus-within:opacity-100">
            <div className="pointer-events-auto rounded-md border border-hairline bg-surface p-0.5 shadow-sm">
              <ArticleQuickActions
                ticketId={ticketId}
                article={article}
                canNote={canNote}
                canDelete={canDelete}
                compact
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/** Collapsed: stripped plain-text preview (cheap, no iframe). Expanded: the
 * real `ArticleBodyRenderer` (HTML iframe or `<pre>`), same as the split
 * view's reading pane. */
function BubbleBody({ ticketId, article }: { ticketId: number; article: ArticleListItem }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const bodyQ = useArticleBody(ticketId, article.id);

  if (bodyQ.isLoading) return <p className="text-sm text-muted">…</p>;
  if (!bodyQ.data) return null;

  const plain = bodyQ.data.is_html ? stripHtml(bodyQ.data.body) : decodeEntities(bodyQ.data.body);
  const isLong = plain.length > PREVIEW_CHARS;

  return (
    <div>
      {expanded ? (
        <ArticleBodyRenderer body={bodyQ.data.body} isHtml={bodyQ.data.is_html} className="text-sm" />
      ) : (
        <p
          className={cn(
            "whitespace-pre-wrap text-ink",
            // Mail bodies are plain text more often than not — monospace keeps
            // quoting/indentation/ASCII tables legible inside a bubble.
            bodyQ.data.is_html ? "text-sm" : "font-mono text-[12.5px] leading-relaxed",
          )}
        >
          {isLong ? `${plain.slice(0, PREVIEW_CHARS)}…` : plain}
        </p>
      )}
      {isLong && (
        <button
          type="button"
          data-testid={`conversation-expand-${article.id}`}
          onClick={() => setExpanded((v) => !v)}
          className="mt-1 text-[11px] font-medium text-accent hover:underline"
        >
          {expanded ? `${t("ticket.showLess")} ⌃` : `${t("ticket.showFull")} ⌄`}
        </button>
      )}
    </div>
  );
}

function NotePill({
  ticketId,
  article,
  canDelete,
  locale,
}: {
  ticketId: number;
  article: ArticleListItem;
  canDelete: boolean;
  locale: string;
}) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const bodyQ = useArticleBody(ticketId, article.id);
  const del = useDeleteArticleNote(ticketId, article.id);
  const senderName = senderDisplayName(article.from_address) || t("ticket.unknownSender");
  const plain = bodyQ.data
    ? bodyQ.data.is_html
      ? stripHtml(bodyQ.data.body)
      : decodeEntities(bodyQ.data.body)
    : "";

  return (
    <div className="flex flex-col items-center gap-1" data-testid={`conversation-note-${article.id}`}>
      <div className="group relative max-w-[80%]">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className={cn(
            "w-full rounded-2xl border border-dashed border-escalation/50 bg-escalation/10 px-3 py-1.5 text-xs text-escalation",
            expanded ? "text-left" : "text-center",
          )}
        >
          <p className="font-semibold">
            📝 {t("ticket.internalNote")} · {senderName} · {formatDateTime(article.create_time, locale)}
          </p>
          <p className={cn(expanded ? "mt-1 whitespace-pre-wrap" : "truncate")}>
            {expanded ? plain : plain.slice(0, 60)}
          </p>
        </button>
        {canDelete && (
          <button
            type="button"
            aria-label={t("ticket.deleteNote")}
            data-testid={`article-delete-${article.id}`}
            onClick={(e) => {
              e.stopPropagation();
              void del.requestDelete();
            }}
            className="pointer-events-none absolute -top-2 right-1 inline-flex h-5 w-5 items-center justify-center rounded-full border border-hairline bg-surface text-[10px] text-muted opacity-0 transition-opacity duration-100 hover:text-danger group-hover:pointer-events-auto group-hover:opacity-100 group-focus-within:pointer-events-auto group-focus-within:opacity-100"
          >
            ✕
          </button>
        )}
      </div>
      {del.errorMessage && (
        <p className="text-xs text-danger" data-testid={`article-delete-error-${article.id}`}>
          {del.errorMessage}
        </p>
      )}
      {canDelete && del.dialog}
    </div>
  );
}
