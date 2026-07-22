import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, type ArticleListItem } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { decodeEntities, stripHtml } from "@/lib/html";
import { groupByDay } from "@/lib/article";
import {
  channelIcon,
  emailFromAddress,
  initialsFor,
  isInternalNote,
  senderRingClass,
} from "@/lib/articleChannel";
import { cn } from "@/lib/cn";
import { Avatar } from "@/components/ui/Avatar";
import { ArticleBodyRenderer } from "./ArticleBodyRenderer";
import { ArticleQuickActions } from "./ArticleQuickActions";

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
  locale,
}: {
  ticketId: number;
  /** Already filtered, chronological (oldest→newest) — see
   * `useArticleListState().chronological`. */
  articles: ArticleListItem[];
  canNote: boolean;
  locale: string;
}) {
  const { t } = useTranslation();
  const bottomRef = useRef<HTMLDivElement>(null);

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
            {g.items.map((a) =>
              isInternalNote(a) ? (
                <NotePill key={a.id} ticketId={ticketId} article={a} locale={locale} />
              ) : (
                <Bubble
                  key={a.id}
                  ticketId={ticketId}
                  article={a}
                  canNote={canNote}
                  locale={locale}
                  side={bubbleSide(a.sender_type)}
                />
              ),
            )}
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
  locale,
  side,
}: {
  ticketId: number;
  article: ArticleListItem;
  canNote: boolean;
  locale: string;
  side: "left" | "right";
}) {
  const { t } = useTranslation();
  const senderName = article.from_address || t("ticket.unknownSender");
  const tone =
    side === "right"
      ? "bg-accent/15"
      : (article.sender_type || "").toLowerCase() === "system"
        ? "bg-surface-subtle"
        : "bg-green/10";

  return (
    <div
      className={cn("group flex", side === "right" ? "justify-end" : "justify-start")}
      data-testid={`conversation-bubble-${article.id}`}
      data-side={side}
    >
      <div className={cn("flex max-w-[76%] items-start gap-2", side === "right" && "flex-row-reverse")}>
        <span
          className={cn(
            "mt-0.5 shrink-0 rounded-full",
            senderRingClass(article.sender_type, article.communication_channel_id),
          )}
        >
          <Avatar
            initials={initialsFor(article)}
            email={emailFromAddress(article.from_address)}
            size={24}
          />
        </span>
        <div className="relative min-w-0">
          <div className={cn("space-y-1 rounded-2xl px-3 py-2", tone)}>
            <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted">
              <span className="font-semibold text-ink">{senderName}</span>
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
              <ArticleQuickActions ticketId={ticketId} article={article} canNote={canNote} compact />
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
        <p className="whitespace-pre-wrap text-sm text-ink">
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
  locale,
}: {
  ticketId: number;
  article: ArticleListItem;
  locale: string;
}) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const bodyQ = useArticleBody(ticketId, article.id);
  const senderName = article.from_address || t("ticket.unknownSender");
  const plain = bodyQ.data
    ? bodyQ.data.is_html
      ? stripHtml(bodyQ.data.body)
      : decodeEntities(bodyQ.data.body)
    : "";

  return (
    <div className="flex justify-center" data-testid={`conversation-note-${article.id}`}>
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className={cn(
          "max-w-[80%] rounded-2xl border border-dashed border-escalation/50 bg-escalation/10 px-3 py-1.5 text-xs text-escalation",
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
    </div>
  );
}
