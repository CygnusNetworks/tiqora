import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toBcp47 } from "@/i18n";
import { api, type ArticleListItem } from "@/lib/api";
import { formatDateTime, formatBytes } from "@/lib/format";
import { fileTypeInfo } from "@/lib/filetype";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { postComposerExtras } from "@/lib/composerExtras";
import type { PickedMention } from "@/lib/mentions";
import { ArticleBodyRenderer } from "./ArticleBodyRenderer";
import { ComposerTimeChip } from "./ComposerTimeChip";
import { MentionTextarea } from "./MentionTextarea";
import { ReplyDialog } from "./ReplyDialog";
import {
  BounceDialog,
  ForwardDialog,
  SplitDialog,
} from "./ArticleActionDialogs";
import { cn } from "@/lib/cn";
import { articleSortKey, groupByDay } from "@/lib/article";
import { formatFromAddress, formatToAddresses } from "@/lib/articleChannel";

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

export function ArticleTimeline({
  ticketId,
  onComposingChange,
  descending = true,
  noteOpen,
  onNoteOpenChange,
  canNote = true,
}: {
  ticketId: number;
  /** Reported whenever the reply/note composer opens or closes, so the
   * parent page can reflect it in its presence heartbeat (see
   * TicketZoomPage). */
  onComposingChange?: (composing: boolean) => void;
  /** Newest-first when true (default). Controlled from the ticket-zoom ⋮ menu. */
  descending?: boolean;
  /** Controlled open state for the internal-note composer (⋮ menu). */
  noteOpen?: boolean;
  onNoteOpenChange?: (open: boolean) => void;
  /** Whether the agent may reply / add notes (``note`` permission). */
  canNote?: boolean;
}) {
  const { t, i18n } = useTranslation();
  const locale = toBcp47(i18n.language);
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});

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
  const groups = groupByDay(sorted, locale);

  return (
    <div className="space-y-6" data-testid="article-timeline">
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
                          {formatFromAddress(article.from_address)}
                          {article.to_address ? ` → ${formatToAddresses(article.to_address)}` : ""}
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
                        <ArticleActions
                          ticketId={ticketId}
                          article={article}
                          canNote={canNote}
                        />
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
        ))
      )}
      {canNote && (
        <ArticleComposer
          ticketId={ticketId}
          articles={articles}
          onComposingChange={onComposingChange}
          open={noteOpen}
          onOpenChange={onNoteOpenChange}
        />
      )}
    </div>
  );
}

/** Per-article action row: Reply / Reply-all / Forward / Bounce / Split. */
function ArticleActions({
  ticketId,
  article,
  canNote = true,
}: {
  ticketId: number;
  article: ArticleListItem;
  canNote?: boolean;
}) {
  const { t } = useTranslation();
  const [dialog, setDialog] = useState<
    "reply" | "replyAll" | "forward" | "bounce" | "split" | null
  >(null);
  const hasMultipleRecipients =
    (article.to_address ?? "").includes(",") || Boolean(article.to_address && article.from_address);
  const noPerm = t("ticket.toolbar.noPermission");

  return (
    <div
      className="flex flex-wrap items-center gap-1.5 border-t border-hairline/60 pt-2"
      data-testid={`article-actions-${article.id}`}
    >
      <span title={!canNote ? noPerm : undefined} className="inline-flex">
        <Button
          size="sm"
          variant="secondary"
          disabled={!canNote}
          onClick={() => setDialog("reply")}
        >
          {t("ticket.reply")}
        </Button>
      </span>
      {hasMultipleRecipients && (
        <span title={!canNote ? noPerm : undefined} className="inline-flex">
          <Button
            size="sm"
            variant="secondary"
            disabled={!canNote}
            onClick={() => setDialog("replyAll")}
          >
            {t("ticket.replyAll")}
          </Button>
        </span>
      )}
      <Button size="sm" variant="ghost" onClick={() => setDialog("forward")}>
        {t("ticket.forward")}
      </Button>
      <Button size="sm" variant="ghost" onClick={() => setDialog("bounce")}>
        {t("ticket.bounce")}
      </Button>
      <Button size="sm" variant="ghost" onClick={() => setDialog("split")}>
        {t("ticket.splitAction")}
      </Button>

      {canNote && (
        <ReplyDialog
          ticketId={ticketId}
          articleId={article.id}
          replyAll={dialog === "replyAll"}
          open={dialog === "reply" || dialog === "replyAll"}
          onClose={() => setDialog(null)}
        />
      )}
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
export function ArticleComposer({
  ticketId,
  articles,
  onComposingChange,
  open: openProp,
  onOpenChange,
}: {
  ticketId: number;
  articles: ArticleListItem[];
  onComposingChange?: (composing: boolean) => void;
  /** Controlled open (ticket-zoom ⋮ menu). Uncontrolled when omitted. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const rootRef = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLTextAreaElement>(null);
  const [openLocal, setOpenLocal] = useState(false);
  const controlled = openProp !== undefined;
  const open = controlled ? openProp : openLocal;
  const setOpen = (next: boolean) => {
    onOpenChange?.(next);
    if (!controlled) setOpenLocal(next);
  };
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [openedAtMaxId, setOpenedAtMaxId] = useState(0);
  // An internal note is the most common place to pull a colleague in, so the
  // mention picker and the minute chip live here rather than in a side panel.
  const [mentions, setMentions] = useState<PickedMention[]>([]);
  const [timeUnits, setTimeUnits] = useState("");
  const [extrasFailed, setExtrasFailed] = useState<Array<"mentions" | "time">>([]);

  useEffect(() => {
    onComposingChange?.(open);
  }, [open, onComposingChange]);

  // Snapshot max article id when the composer opens (controlled or not).
  useEffect(() => {
    if (open) setOpenedAtMaxId(maxArticleId(articles));
    // Only re-snapshot on open transition; articles changing later is the
    // stale-warning signal, not a re-baseline.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Header "+ Notiz" / ⋮ menu open the composer at the bottom of a potentially
  // long article list — without scroll+focus it looks like a dead button.
  useEffect(() => {
    if (!open) return;
    rootRef.current?.scrollIntoView?.({ behavior: "smooth", block: "nearest" });
    const frame = window.requestAnimationFrame(() => {
      bodyRef.current?.focus();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [open]);

  const finishSend = () => {
    setOpen(false);
    setSubject("");
    setBody("");
    setMentions([]);
    setTimeUnits("");
    setExtrasFailed([]);
  };

  const sendMutation = useMutation({
    mutationFn: async () => {
      await api.createArticle(ticketId, {
        sender_type: "agent",
        subject: subject || t("ticket.composerNote"),
        body,
        content_type: "text/plain; charset=utf-8",
        // Bottom composer is internal-note only; customer replies use the
        // per-article reply dialog.
        channel: "note",
        is_visible_for_customer: false,
      });
      return postComposerExtras(ticketId, { body, mentions, timeUnits, queryClient });
    },
    onSuccess: (extras) => {
      void queryClient.invalidateQueries({ queryKey: ["tickets", ticketId, "articles"] });
      void queryClient.invalidateQueries({ queryKey: ["tickets", ticketId] });
      if (extras.failed.length > 0) {
        // The note is saved; keep the composer open so the failed booking or
        // mention can be retried on its own.
        setExtrasFailed(extras.failed);
        return;
      }
      finishSend();
    },
  });

  const retryExtrasMutation = useMutation({
    mutationFn: () =>
      postComposerExtras(ticketId, {
        body,
        mentions: extrasFailed.includes("mentions") ? mentions : [],
        timeUnits: extrasFailed.includes("time") ? timeUnits : "",
        queryClient,
      }),
    onSuccess: (extras) => {
      setExtrasFailed(extras.failed);
      if (extras.failed.length === 0) finishSend();
    },
  });

  if (!open) {
    // When controlled by the ticket-zoom ⋮ menu, hide the redundant open button.
    if (controlled) return null;
    return (
      <div>
        <Button
          variant="secondary"
          size="sm"
          data-testid="composer-open"
          onClick={() => setOpen(true)}
        >
          {t("ticket.composerNote")}
        </Button>
      </div>
    );
  }

  const staleWarning = maxArticleId(articles) > openedAtMaxId;

  return (
    <div
      ref={rootRef}
      className="space-y-2 rounded-lg border border-hairline bg-surface p-3"
      data-testid="article-composer"
    >
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
      <MentionTextarea
        textareaRef={bodyRef}
        value={body}
        onChange={setBody}
        mentions={mentions}
        onMentionsChange={setMentions}
        placeholder={t("ticket.composerBodyPlaceholder")}
        ariaLabel={t("ticket.composerNote")}
        testId="composer-body"
        readOnly={extrasFailed.length > 0}
        rows={4}
      />
      {extrasFailed.length > 0 && (
        <p
          className="rounded border border-escalation/30 bg-escalation/15 px-2 py-1 text-xs text-escalation"
          data-testid="composer-extras-error"
        >
          {t(
            extrasFailed.length === 2
              ? "ticket.extrasFailedBoth"
              : extrasFailed[0] === "mentions"
                ? "ticket.extrasFailedMentions"
                : "ticket.extrasFailedTime",
          )}
        </p>
      )}
      <div className="flex items-center gap-1.5">
        <ComposerTimeChip value={timeUnits} onChange={setTimeUnits} testId="composer-time" />
        <span className="flex-1" />
        <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>
          {t("ticket.composerCancel")}
        </Button>
        {extrasFailed.length > 0 ? (
          <Button
            variant="primary"
            size="sm"
            data-testid="composer-extras-retry"
            disabled={retryExtrasMutation.isPending}
            onClick={() => retryExtrasMutation.mutate()}
          >
            {t("ticket.extrasRetry")}
          </Button>
        ) : (
          <Button
            variant="primary"
            size="sm"
            data-testid="composer-send"
            disabled={!body.trim() || sendMutation.isPending}
            onClick={() => sendMutation.mutate()}
          >
            {t("ticket.composerSend")}
          </Button>
        )}
      </div>
    </div>
  );
}

export function ArticleBodyLoader({
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

export function AttachmentList({
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
  // `inline` lands in the generated client with the next OpenAPI regen —
  // typed optional here so this renders correctly with either client version.
  type Att = NonNullable<typeof attQ.data>[number] & { inline?: boolean };
  const items: Att[] = attQ.data ?? [];
  if (items.length === 0) return null;

  // Embedded cid: parts (signature logos etc.) are noise next to real
  // attachments — tuck them behind a small disclosure instead.
  const real = items.filter((a) => !a.inline);
  const inline = items.filter((a) => a.inline);

  const renderItem = (a: (typeof items)[number]) => {
    const type = fileTypeInfo(a.content_type, a.filename);
    return (
      <li key={a.id}>
        <a
          className="group inline-flex max-w-full items-center gap-2 text-xs"
          href={api.attachmentDownloadUrl(ticketId, articleId, a.id, true)}
          download={a.filename ?? undefined}
          // Raw MIME stays reachable for the curious — as a tooltip, not
          // as list clutter.
          title={a.content_type ?? undefined}
          data-testid={`attachment-${a.id}`}
        >
          <span
            className={`inline-flex h-5 min-w-[2.4rem] shrink-0 items-center justify-center rounded px-1 text-[10px] font-bold tracking-wide ${type.className}`}
            data-testid={`attachment-type-${a.id}`}
          >
            {type.label}
          </span>
          <span className="truncate font-medium text-ink group-hover:text-accent group-hover:underline">
            {a.filename || `attachment-${a.id}`}
          </span>
          <span className="shrink-0 tabular-nums text-muted">{formatBytes(a.content_size)}</span>
        </a>
      </li>
    );
  };

  if (real.length === 0 && inline.length === 0) return null;

  return (
    <div data-testid="attachment-list">
      {real.length > 0 && (
        <>
          <h4 className="mb-1 text-xs font-semibold text-muted">{t("ticket.attachments")}</h4>
          <ul className="space-y-1">{real.map(renderItem)}</ul>
        </>
      )}
      {inline.length > 0 && (
        <details className="mt-1" data-testid="attachment-inline-group">
          <summary className="cursor-pointer select-none text-xs text-muted hover:text-accent">
            {t("ticket.inlineAttachments", { count: inline.length })}
          </summary>
          <ul className="mt-1 space-y-1">{inline.map(renderItem)}</ul>
        </details>
      )}
    </div>
  );
}
