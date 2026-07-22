import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, type ArticleListItem } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { decodeEntities, stripHtml } from "@/lib/html";
import {
  avatarTone,
  channelIcon,
  emailFromAddress,
  formatFromAddress,
  formatToAddresses,
  initialsFor,
  senderDisplayName,
} from "@/lib/articleChannel";
import { cn } from "@/lib/cn";
import { Avatar } from "@/components/ui/Avatar";
import { Badge } from "@/components/ui/Badge";
import { ArticleQuickActions } from "./ArticleQuickActions";
import { ArticleBodyLoader, AttachmentList } from "./ArticleTimeline";
import type { ArticleListState } from "./useArticleListState";

/**
 * Article list "Variante 3 — Liste + Lesebereich": a master-detail split.
 * All filter/sort/selection state lives in `useArticleListState` (shared
 * with the conversation view via the ArticleMasterDetail orchestrator) —
 * this component is purely presentational over that state.
 */
export function ArticleSplitView({
  ticketId,
  canNote,
  locale,
  state,
}: {
  ticketId: number;
  canNote: boolean;
  locale: string;
  state: ArticleListState;
}) {
  const { t } = useTranslation();
  const { sorted, selectedId, setSelectedId, selected, onListKeyDown } = state;

  if (sorted.length === 0) {
    return <p className="text-sm text-muted">{t("ticket.noArticles")}</p>;
  }

  return (
    <div className="flex flex-col gap-3 md:flex-row" data-testid="article-master-detail">
      <div
        role="listbox"
        aria-label={t("ticket.articles")}
        tabIndex={0}
        onKeyDown={onListKeyDown}
        data-testid="article-list"
        className="max-h-[40vh] shrink-0 space-y-1 overflow-y-auto rounded-lg border border-hairline bg-surface p-1.5 md:max-h-none md:w-[300px]"
      >
        {sorted.map((a) => (
          <ArticleListRow
            key={a.id}
            ticketId={ticketId}
            article={a}
            locale={locale}
            selected={a.id === selectedId}
            onSelect={() => setSelectedId(a.id)}
          />
        ))}
      </div>
      <div className="min-w-0 flex-1" data-testid="article-reader">
        {selected ? (
          <ArticleReader ticketId={ticketId} article={selected} canNote={canNote} locale={locale} />
        ) : (
          <p className="text-sm text-muted">{t("ticket.noArticles")}</p>
        )}
      </div>
    </div>
  );
}

function ArticleListRow({
  ticketId,
  article,
  locale,
  selected,
  onSelect,
}: {
  ticketId: number;
  article: ArticleListItem;
  locale: string;
  selected: boolean;
  onSelect: () => void;
}) {
  const { t } = useTranslation();
  const senderName = senderDisplayName(article.from_address) || t("ticket.unknownSender");
  return (
    <div
      role="option"
      aria-selected={selected}
      data-testid={`article-list-item-${article.id}`}
      onClick={onSelect}
      className={cn(
        "flex cursor-pointer gap-2 rounded-md border-l-2 px-2 py-1.5",
        selected ? "border-l-accent bg-accent/10" : "border-l-transparent hover:bg-surface-subtle",
      )}
    >
      <Avatar
        initials={initialsFor(article)}
        email={emailFromAddress(article.from_address)}
        tone={avatarTone(article.sender_type)}
        size={24}
        className="mt-0.5"
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
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
          <span className="min-w-0 flex-1 truncate text-xs font-semibold text-ink">{senderName}</span>
          <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted">
            {formatDateTime(article.create_time, locale)}
          </span>
        </div>
        <p className="truncate text-[11px] text-muted">
          <span aria-hidden>{channelIcon(article.communication_channel_id)}</span>{" "}
          <ArticlePreview ticketId={ticketId} article={article} />
        </p>
      </div>
    </div>
  );
}

/** Fetches the article body (same query key as `ArticleBodyLoader`, so
 * opening it in the reading pane afterwards reuses the cached response) and
 * shows a stripped ~80-char plain-text preview. */
function ArticlePreview({ ticketId, article }: { ticketId: number; article: ArticleListItem }) {
  const { t } = useTranslation();
  const bodyQ = useQuery({
    queryKey: ["tickets", ticketId, "articles", article.id, "body"],
    queryFn: () => api.getArticleBody(ticketId, article.id),
  });
  if (bodyQ.isLoading) return <span>…</span>;
  if (!bodyQ.data) return <span>{t("ticket.noSubject")}</span>;
  const plain = bodyQ.data.is_html ? stripHtml(bodyQ.data.body) : decodeEntities(bodyQ.data.body);
  return <span>{plain.slice(0, 80) || t("ticket.noSubject")}</span>;
}

function ArticleReader({
  ticketId,
  article,
  canNote,
  locale,
}: {
  ticketId: number;
  article: ArticleListItem;
  canNote: boolean;
  locale: string;
}) {
  const { t } = useTranslation();
  return (
    <div className="space-y-3 rounded-lg border border-hairline bg-surface p-3">
      <div className="space-y-1.5 border-b border-hairline pb-2">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-ink">
              {article.subject || t("ticket.noSubject")}
            </p>
            <p className="truncate text-xs text-muted">
              {formatFromAddress(article.from_address)}
              {article.to_address ? ` → ${formatToAddresses(article.to_address)}` : ""}
            </p>
          </div>
          <span className="shrink-0 font-mono text-xs tabular-nums text-muted" title={t("ticket.readerDetails")}>
            {formatDateTime(article.create_time, locale)}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge tone={article.is_visible_for_customer ? "success" : "muted"}>
            {article.is_visible_for_customer ? t("ticket.visibleCustomer") : t("ticket.internal")}
          </Badge>
          <Badge tone="default">{channelIcon(article.communication_channel_id)}</Badge>
        </div>
      </div>
      <ArticleQuickActions
        ticketId={ticketId}
        article={article}
        canNote={canNote}
        replyTestId="article-reader-reply"
      />
      <ArticleBodyLoader ticketId={ticketId} articleId={article.id} />
      <AttachmentList ticketId={ticketId} articleId={article.id} />
    </div>
  );
}
