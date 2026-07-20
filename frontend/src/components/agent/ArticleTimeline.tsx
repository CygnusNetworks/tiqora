import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, type ArticleListItem } from "@/lib/api";
import { formatDateTime, formatBytes } from "@/lib/format";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { ArticleBodyRenderer } from "./ArticleBodyRenderer";
import { ReplyDialog } from "./ReplyDialog";
import {
  BounceDialog,
  ForwardDialog,
  SplitDialog,
} from "./ArticleActionDialogs";
import { cn } from "@/lib/cn";

function senderTone(
  senderType: string | null | undefined,
): "accent" | "muted" | "success" | "default" {
  const s = (senderType || "").toLowerCase();
  if (s === "customer") return "accent";
  if (s === "agent") return "success";
  if (s === "system") return "muted";
  return "default";
}

/** Background tint + border classes keyed on sender type (primary cue). */
function senderTintClass(senderType: string | null | undefined): string {
  const s = (senderType || "").toLowerCase();
  if (s === "customer") return "bg-article-customer border-article-customer-border";
  if (s === "system") return "bg-article-system border-hairline";
  return "bg-article-agent border-hairline";
}

/** Sort key: incoming_time (epoch seconds) preferred, else create_time. */
function articleSortKey(a: ArticleListItem): number {
  if (typeof a.incoming_time === "number" && a.incoming_time > 0) {
    return a.incoming_time * 1000;
  }
  return new Date(a.create_time).getTime();
}

function dayKey(iso: string, locale: string): string {
  const d = new Date(iso);
  return new Intl.DateTimeFormat(locale, { dateStyle: "full" }).format(d);
}

export function ArticleTimeline({
  ticketId,
  onComposingChange,
}: {
  ticketId: number;
  /** Reported whenever the reply/note composer opens or closes, so the
   * parent page can reflect it in its presence heartbeat (see
   * TicketZoomPage). */
  onComposingChange?: (composing: boolean) => void;
}) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  // Default newest-first (descending); toggle flips to oldest-first.
  const [descending, setDescending] = useState(true);

  const articlesQ = useQuery({
    queryKey: ["tickets", ticketId, "articles"],
    queryFn: () => api.listArticles(ticketId),
  });

  const articles = articlesQ.data ?? [];

  if (articlesQ.isLoading) {
    return (
      <div className="flex justify-center py-8">
        <Spinner />
      </div>
    );
  }

  // Chronological sort by incoming/create time, respecting the direction.
  const sorted = [...articles].sort((a, b) => {
    const diff = articleSortKey(a) - articleSortKey(b);
    return descending ? -diff : diff;
  });

  // Group by day (order follows the sorted list).
  const groups: { day: string; items: ArticleListItem[] }[] = [];
  for (const a of sorted) {
    const day = dayKey(a.create_time, locale);
    const last = groups[groups.length - 1];
    if (last && last.day === day) last.items.push(a);
    else groups.push({ day, items: [a] });
  }

  return (
    <div className="space-y-6" data-testid="article-timeline">
      {articles.length > 0 && (
        <div className="flex justify-end">
          <Button
            variant="secondary"
            size="sm"
            data-testid="article-sort-toggle"
            onClick={() => setDescending((d) => !d)}
          >
            {descending ? t("ticket.sortNewestFirst") : t("ticket.sortOldestFirst")}
          </Button>
        </div>
      )}
      {articles.length === 0 ? (
        <p className="text-sm text-muted">{t("ticket.noArticles")}</p>
      ) : (
        groups.map((g) => (
          <section key={g.day}>
            <h3 className="mb-2 font-mono text-xs font-semibold uppercase tracking-wide text-muted">
              {g.day}
            </h3>
            <ul className="space-y-3">
              {g.items.map((article) => {
                const open = expanded[article.id] ?? false;
                return (
                  <li
                    key={article.id}
                    className={cn(
                      "rounded-lg border",
                      senderTintClass(article.sender_type),
                    )}
                    data-sender={(article.sender_type || "unknown").toLowerCase()}
                    data-testid={`article-${article.id}`}
                  >
                    <button
                      type="button"
                      className="flex w-full flex-wrap items-start gap-2 px-3 py-2 text-left"
                      onClick={() =>
                        setExpanded((m) => ({ ...m, [article.id]: !open }))
                      }
                    >
                      <span
                        className={cn(
                          "mt-0.5 text-xs text-muted transition",
                          open && "rotate-90",
                        )}
                      >
                        ▶
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <Badge tone={senderTone(article.sender_type)}>
                            {article.sender_type || t("ticket.unknownSender")}
                          </Badge>
                          <Badge
                            tone={
                              article.is_visible_for_customer ? "success" : "muted"
                            }
                          >
                            {article.is_visible_for_customer
                              ? t("ticket.visibleCustomer")
                              : t("ticket.internal")}
                          </Badge>
                          <span className="font-mono text-xs tabular-nums text-muted">
                            {formatDateTime(article.create_time, locale)}
                          </span>
                        </div>
                        <p className="mt-0.5 truncate text-sm font-medium text-ink">
                          {article.subject || t("ticket.noSubject")}
                        </p>
                        <p className="truncate text-xs text-muted">
                          {article.from_address}
                          {article.to_address ? ` → ${article.to_address}` : ""}
                        </p>
                      </div>
                    </button>
                    {open && (
                      <div className="space-y-3 border-t border-hairline px-3 py-3">
                        <ArticleBodyLoader
                          ticketId={ticketId}
                          articleId={article.id}
                        />
                        <AttachmentList
                          ticketId={ticketId}
                          articleId={article.id}
                        />
                        <ArticleActions ticketId={ticketId} article={article} />
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
        ))
      )}
      <ArticleComposer
        ticketId={ticketId}
        articles={articles}
        onComposingChange={onComposingChange}
      />
    </div>
  );
}

/** Per-article action row: Reply / Reply-all / Forward / Bounce / Split. */
function ArticleActions({
  ticketId,
  article,
}: {
  ticketId: number;
  article: ArticleListItem;
}) {
  const { t } = useTranslation();
  const [dialog, setDialog] = useState<
    "reply" | "replyAll" | "forward" | "bounce" | "split" | null
  >(null);
  const hasMultipleRecipients =
    (article.to_address ?? "").includes(",") || Boolean(article.to_address && article.from_address);

  return (
    <div
      className="flex flex-wrap items-center gap-1.5 border-t border-hairline/60 pt-2"
      data-testid={`article-actions-${article.id}`}
    >
      <Button size="sm" variant="secondary" onClick={() => setDialog("reply")}>
        {t("ticket.reply")}
      </Button>
      {hasMultipleRecipients && (
        <Button size="sm" variant="secondary" onClick={() => setDialog("replyAll")}>
          {t("ticket.replyAll")}
        </Button>
      )}
      <Button size="sm" variant="ghost" onClick={() => setDialog("forward")}>
        {t("ticket.forward")}
      </Button>
      <Button size="sm" variant="ghost" onClick={() => setDialog("bounce")}>
        {t("ticket.bounce")}
      </Button>
      <Button size="sm" variant="ghost" onClick={() => setDialog("split")}>
        {t("ticket.split")}
      </Button>

      <ReplyDialog
        ticketId={ticketId}
        articleId={article.id}
        replyAll={dialog === "replyAll"}
        open={dialog === "reply" || dialog === "replyAll"}
        onClose={() => setDialog(null)}
      />
      <ForwardDialog
        ticketId={ticketId}
        articleId={article.id}
        open={dialog === "forward"}
        onClose={() => setDialog(null)}
      />
      <BounceDialog
        ticketId={ticketId}
        articleId={article.id}
        open={dialog === "bounce"}
        onClose={() => setDialog(null)}
      />
      <SplitDialog
        ticketId={ticketId}
        articleId={article.id}
        open={dialog === "split"}
        onClose={() => setDialog(null)}
      />
    </div>
  );
}

function maxArticleId(articles: ArticleListItem[]): number {
  return articles.reduce((max, a) => Math.max(max, a.id), 0);
}

/**
 * Reply/note composer with a minimal "another agent replied" guard: it
 * snapshots the highest article id at the moment it opens, and — since SSE
 * `ticket_changed` messages invalidate the articles query and cause a
 * refetch — shows a banner if that refetch ever yields a higher id while
 * still composing. No diffing beyond that single max-id comparison.
 */
function ArticleComposer({
  ticketId,
  articles,
  onComposingChange,
}: {
  ticketId: number;
  articles: ArticleListItem[];
  onComposingChange?: (composing: boolean) => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [openedAtMaxId, setOpenedAtMaxId] = useState(0);

  useEffect(() => {
    onComposingChange?.(open);
  }, [open, onComposingChange]);

  const sendMutation = useMutation({
    mutationFn: () =>
      api.createArticle(ticketId, {
        sender_type: "agent",
        subject: subject || t("ticket.composerNote"),
        body,
        content_type: "text/plain; charset=utf-8",
        // Bottom composer is internal-note only; customer replies use the
        // per-article reply dialog.
        channel: "note",
        is_visible_for_customer: false,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["tickets", ticketId, "articles"] });
      void queryClient.invalidateQueries({ queryKey: ["tickets", ticketId] });
      setOpen(false);
      setSubject("");
      setBody("");
    },
  });

  if (!open) {
    return (
      <div>
        <Button
          variant="secondary"
          size="sm"
          data-testid="composer-open"
          onClick={() => {
            setOpenedAtMaxId(maxArticleId(articles));
            setOpen(true);
          }}
        >
          {t("ticket.composerNote")}
        </Button>
      </div>
    );
  }

  const staleWarning = maxArticleId(articles) > openedAtMaxId;

  return (
    <div className="space-y-2 rounded-lg border border-hairline bg-surface p-3" data-testid="article-composer">
      {staleWarning && (
        <p
          className="rounded border border-escalation/30 bg-escalation/15 px-2 py-1 text-xs text-escalation"
          data-testid="composer-stale-warning"
        >
          {t("ticket.composeWarning")}
        </p>
      )}
      <p className="text-xs font-medium text-muted">{t("ticket.composerNote")}</p>
      <input
        type="text"
        value={subject}
        onChange={(e) => setSubject(e.target.value)}
        placeholder={t("ticket.composerSubjectPlaceholder")}
        className="w-full rounded border border-hairline bg-surface px-2 py-1.5 text-sm text-ink placeholder:text-muted focus:outline-none focus:ring-1 focus:ring-accent"
      />
      <textarea
        value={body}
        onChange={(e) => setBody(e.target.value)}
        placeholder={t("ticket.composerBodyPlaceholder")}
        rows={4}
        className="w-full rounded border border-hairline bg-surface px-2 py-1.5 text-sm text-ink placeholder:text-muted focus:outline-none focus:ring-1 focus:ring-accent"
      />
      <div className="flex items-center gap-1.5">
        <Button
          variant="primary"
          size="sm"
          data-testid="composer-send"
          disabled={!body.trim() || sendMutation.isPending}
          onClick={() => sendMutation.mutate()}
        >
          {t("ticket.composerSend")}
        </Button>
        <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>
          {t("ticket.composerCancel")}
        </Button>
      </div>
    </div>
  );
}

function ArticleBodyLoader({
  ticketId,
  articleId,
}: {
  ticketId: number;
  articleId: number;
}) {
  const bodyQ = useQuery({
    queryKey: ["tickets", ticketId, "articles", articleId, "body"],
    queryFn: () => api.getArticleBody(ticketId, articleId),
  });

  if (bodyQ.isLoading) {
    return (
      <div className="flex justify-center py-4">
        <Spinner />
      </div>
    );
  }
  if (bodyQ.isError || !bodyQ.data) {
    return <p className="text-xs text-danger">Failed to load body</p>;
  }
  return (
    <ArticleBodyRenderer body={bodyQ.data.body} isHtml={bodyQ.data.is_html} />
  );
}

function AttachmentList({
  ticketId,
  articleId,
}: {
  ticketId: number;
  articleId: number;
}) {
  const { t } = useTranslation();
  const attQ = useQuery({
    queryKey: ["tickets", ticketId, "articles", articleId, "attachments"],
    queryFn: () => api.listAttachments(ticketId, articleId),
  });

  if (attQ.isLoading) return null;
  const items = attQ.data ?? [];
  if (items.length === 0) return null;

  return (
    <div data-testid="attachment-list">
      <h4 className="mb-1 text-xs font-semibold text-muted">
        {t("ticket.attachments")}
      </h4>
      <ul className="space-y-1">
        {items.map((a) => (
          <li key={a.id}>
            <a
              className="inline-flex items-center gap-2 text-xs text-accent hover:underline"
              href={api.attachmentDownloadUrl(ticketId, articleId, a.id, true)}
              download={a.filename ?? undefined}
              data-testid={`attachment-${a.id}`}
            >
              <span>{a.filename || `attachment-${a.id}`}</span>
              <span className="text-muted">
                {formatBytes(a.content_size)} · {a.content_type || "—"}
              </span>
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}
