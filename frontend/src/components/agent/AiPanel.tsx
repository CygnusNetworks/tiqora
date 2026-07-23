import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, ApiError } from "@/lib/api";
import { aiApi } from "@/lib/aiApi";
import { useAuth } from "@/auth/AuthContext";
import { ticketAiApi, type AiDraftOut } from "@/lib/ticketAiApi";
import { articleSortKey } from "@/lib/article";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/cn";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Spinner } from "@/components/ui/Spinner";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { HelpPopover } from "@/components/ui/HelpPopover";
import { ReplyDialog } from "./ReplyDialog";

/**
 * AI panel for the ticket zoom page (plan §3.4 Drafts, §3.5 Summary,
 * Phase B/C). Fetches `GET /tickets/{id}/ai` and renders nothing when
 * neither summary nor manual-assist is available for this agent/queue —
 * agents without ACL access see no trace of the feature.
 *
 * Sibling to `ProcessWidget` in `TicketZoomPage`; matches its panel chrome
 * (`rounded-lg border border-hairline bg-surface p-4`).
 */

/** Max articles rendered as individual coverage dots; beyond that the
 * indicator degrades to a plain "n/m" fraction to avoid a dot wall. */
const MAX_COVERAGE_DOTS = 12;

/** Coverage of the current summary over the ticket's articles: filled dots
 * are summarized, the outline dots arrived later. */
function CoverageDots({ covered, total }: { covered: number; total: number }) {
  if (total === 0) return null;
  if (total > MAX_COVERAGE_DOTS) {
    return (
      <span className="text-[11px] tabular-nums text-muted" data-testid="ai-summary-coverage">
        {covered}/{total}
      </span>
    );
  }
  return (
    <span
      className="inline-flex items-center gap-1"
      data-testid="ai-summary-coverage"
      aria-label={`${covered}/${total}`}
    >
      {Array.from({ length: total }, (_, i) => (
        <span
          key={i}
          className={cn(
            "h-1.5 w-1.5 rounded-full",
            i < covered ? "bg-accent" : "border border-muted/60",
          )}
        />
      ))}
    </span>
  );
}

function DraftKindIcon({ kind }: { kind: string }) {
  const clarify = kind === "clarify";
  return (
    <span
      aria-hidden
      className={cn(
        "flex h-7 w-7 flex-none items-center justify-center rounded-md text-sm",
        clarify ? "bg-escalation/15 text-escalation" : "bg-accent-dim text-accent",
      )}
    >
      {clarify ? "?" : "↩"}
    </span>
  );
}

export function AiPanel({ ticketId, canNote }: { ticketId: number; canNote: boolean }) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const isAdmin = Boolean(user?.is_admin);
  const { confirm, dialog: confirmDialog } = useConfirm();
  const [expandedDraftId, setExpandedDraftId] = useState<number | null>(null);
  const [openTraceId, setOpenTraceId] = useState<number | null>(null);
  const [replyDraft, setReplyDraft] = useState<AiDraftOut | null>(null);

  const stateQ = useQuery({
    queryKey: ["tickets", ticketId, "ai"],
    queryFn: ({ signal }) => ticketAiApi.getState(ticketId, signal),
  });

  // Shares the query key with TicketHeaderActions/ArticleMasterDetail, so in
  // the zoom page this is a cache hit, not a second request. Used for the
  // coverage indicator and as reply-target fallback for drafts without
  // based_on_article_id.
  const articlesQ = useQuery({
    queryKey: ["tickets", ticketId, "articles"],
    queryFn: () => api.listArticles(ticketId),
    enabled: Boolean(stateQ.data?.summary_available) || Boolean(replyDraft),
  });

  const draftMutation = useMutation({
    mutationFn: () => ticketAiApi.requestDraft(ticketId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["tickets", ticketId, "ai"] });
    },
  });

  const summarizeMutation = useMutation({
    mutationFn: () => ticketAiApi.summarize(ticketId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["tickets", ticketId, "ai"] });
    },
  });

  const discardMutation = useMutation({
    mutationFn: (draftId: number) => ticketAiApi.discardDraft(ticketId, draftId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["tickets", ticketId, "ai"] });
    },
  });

  const adminDeleteMutation = useMutation({
    mutationFn: (draftId: number) => aiApi.adminDeleteDraft(draftId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["tickets", ticketId, "ai"] });
    },
  });

  if (stateQ.isLoading || stateQ.isError || !stateQ.data) return null;

  const state = stateQ.data;
  if (!state.manual_assist_available && !state.summary_available) return null;

  const openDrafts = state.drafts.filter((d) => d.status === "open");
  const locale = i18n.language;

  const mapRunError = (error: unknown): string => {
    if (error instanceof ApiError) {
      if (error.status === 423) return t("ticket.ai.errorLocked");
      if (error.status === 403) return t("ticket.ai.errorForbidden");
      if (error.status === 429) return t("ticket.ai.errorRateLimited");
      if (error.status === 409) return error.message || t("ticket.ai.errorDisabled");
    }
    return t("ticket.ai.errorGeneric");
  };

  const articles = articlesQ.data ?? [];
  const upto = state.last_summary_upto_article_id;
  const coveredCount = upto == null ? 0 : articles.filter((a) => a.id <= upto).length;
  const staleCount = upto == null ? 0 : articles.filter((a) => a.id > upto).length;
  const hasSummary = state.summary_body != null;

  const replyArticleId =
    replyDraft?.based_on_article_id ??
    [...articles].sort((a, b) => articleSortKey(b) - articleSortKey(a))[0]?.id ??
    null;

  return (
    <div className="space-y-3 rounded-lg border border-hairline bg-surface p-4" data-testid="ai-panel">
      <h2 className="font-display text-sm font-semibold text-ink">{t("ticket.ai.title")}</h2>

      {state.summary_available && (
        <div className="space-y-2" data-testid="ai-panel-summary">
          <div className="flex items-center justify-between gap-2">
            <span className="inline-flex items-center gap-1.5 text-xs uppercase tracking-wide text-muted">
              {t("ticket.ai.summaryLabel")}
              <HelpPopover title={t("ticket.ai.summaryLabel")} testId="ai-panel-help-summary">
                {t("ticket.ai.help.summary")}
              </HelpPopover>
            </span>
            <Button
              size="sm"
              variant="secondary"
              data-testid="ai-panel-summarize-button"
              disabled={!state.can_summarize || summarizeMutation.isPending}
              onClick={() => summarizeMutation.mutate()}
            >
              {summarizeMutation.isPending ? (
                <Spinner className="h-3.5 w-3.5" />
              ) : hasSummary ? (
                t("ticket.ai.refreshButton")
              ) : (
                t("ticket.ai.summarizeButton")
              )}
            </Button>
          </div>

          {hasSummary ? (
            <div className="space-y-2">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
                {staleCount > 0 ? (
                  <Badge tone="warn" data-testid="ai-summary-stale">
                    {t("ticket.ai.summaryStale", { count: staleCount })}
                  </Badge>
                ) : (
                  <Badge tone="success" data-testid="ai-summary-current">
                    {t("ticket.ai.summaryCurrent")}
                  </Badge>
                )}
                {articles.length > 0 && (
                  <CoverageDots covered={coveredCount} total={articles.length} />
                )}
                {state.summary_created_at && (
                  <span className="text-[11px] text-muted" data-testid="ai-summary-created-at">
                    {t("ticket.ai.summaryCreatedAt", {
                      dateTime: formatDateTime(state.summary_created_at, locale),
                    })}
                  </span>
                )}
              </div>
              <p
                className="whitespace-pre-wrap text-sm text-ink"
                data-testid="ai-panel-summary-body"
              >
                {state.summary_body}
              </p>
            </div>
          ) : (
            <p className="text-sm text-muted" data-testid="ai-panel-summary-empty">
              {t("ticket.ai.summaryEmpty")}
            </p>
          )}
          {summarizeMutation.isSuccess && summarizeMutation.data.status === "up_to_date" && (
            <p className="text-xs text-muted" data-testid="ai-panel-summary-uptodate">
              {t("ticket.ai.summaryUpToDate")}
            </p>
          )}
          {summarizeMutation.isError && (
            <p className="text-xs text-danger" data-testid="ai-panel-summary-error">
              {mapRunError(summarizeMutation.error)}
            </p>
          )}
        </div>
      )}

      {state.manual_assist_available && (
        <div className="space-y-2" data-testid="ai-panel-drafts">
          <div className="flex items-center justify-between gap-2">
            <span className="inline-flex items-center gap-1.5 text-xs uppercase tracking-wide text-muted">
              {t("ticket.ai.draftsLabel")}
              <HelpPopover title={t("ticket.ai.draftsLabel")} testId="ai-panel-help-drafts">
                {t("ticket.ai.help.drafts")}
              </HelpPopover>
            </span>
            <span title={!canNote ? t("ticket.toolbar.noPermission") : undefined}>
              <Button
                size="sm"
                variant="secondary"
                data-testid="ai-panel-create-draft-button"
                disabled={!canNote || draftMutation.isPending}
                onClick={() => draftMutation.mutate()}
              >
                {draftMutation.isPending ? <Spinner className="h-3.5 w-3.5" /> : t("ticket.ai.createDraftButton")}
              </Button>
            </span>
          </div>
          {draftMutation.isPending && (
            <p className="text-xs text-muted">{t("ticket.ai.createDraftHint")}</p>
          )}
          {draftMutation.isError && (
            <p className="text-xs text-danger" data-testid="ai-panel-draft-error">
              {mapRunError(draftMutation.error)}
            </p>
          )}

          {openDrafts.length === 0 ? (
            <p className="text-sm text-muted" data-testid="ai-panel-drafts-empty">
              {t("ticket.ai.draftsEmpty")}
            </p>
          ) : (
            <ul className="space-y-2">
              {openDrafts.map((draft) => {
                const expanded = expandedDraftId === draft.id;
                return (
                  <li
                    key={draft.id}
                    className="space-y-2 rounded-md border border-hairline bg-surface-subtle p-3"
                    data-testid={`ai-panel-draft-${draft.id}`}
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex min-w-0 items-center gap-2.5">
                        <DraftKindIcon kind={draft.kind} />
                        <div className="min-w-0">
                          <div className="text-[13px] font-semibold text-ink">
                            {t(`ticket.ai.draftTitle.${draft.kind}`, {
                              defaultValue: t(`ticket.ai.draftKind.${draft.kind}`, {
                                defaultValue: draft.kind,
                              }),
                            })}
                          </div>
                          <div
                            className="truncate text-[11px] text-muted"
                            data-testid={`ai-panel-draft-meta-${draft.id}`}
                          >
                            {formatDateTime(draft.create_time, locale)}
                            {" · "}
                            {t(`ticket.ai.draftSource.${draft.source}`, {
                              defaultValue: draft.source,
                            })}
                            {draft.based_on_article_id != null && (
                              <>
                                {" · "}
                                {t("ticket.ai.basedOnArticle", {
                                  articleId: draft.based_on_article_id,
                                })}
                              </>
                            )}
                          </div>
                        </div>
                      </div>
                      <div className="flex items-center gap-1.5">
                        {isAdmin && (
                          <Button
                            size="sm"
                            variant="ghost"
                            data-testid={`ai-panel-draft-admin-delete-${draft.id}`}
                            disabled={adminDeleteMutation.isPending}
                            title={t("ticket.ai.adminDeleteDraftHint")}
                            onClick={async () => {
                              const ok = await confirm({
                                title: t("ticket.ai.adminDeleteDraft"),
                                message: t("ticket.ai.adminDeleteConfirm"),
                                variant: "danger",
                              });
                              if (ok) adminDeleteMutation.mutate(draft.id);
                            }}
                          >
                            {t("ticket.ai.adminDeleteDraft")}
                          </Button>
                        )}
                        <Button
                          size="sm"
                          variant="ghost"
                          data-testid={`ai-panel-draft-discard-${draft.id}`}
                          disabled={discardMutation.isPending}
                          onClick={async () => {
                            const ok = await confirm({
                              title: t("ticket.ai.discardDraft"),
                              message: t("ticket.ai.discardConfirm"),
                              variant: "danger",
                            });
                            if (ok) discardMutation.mutate(draft.id);
                          }}
                        >
                          {t("ticket.ai.discardDraft")}
                        </Button>
                        <Button
                          size="sm"
                          variant="primary"
                          data-testid={`ai-panel-draft-use-${draft.id}`}
                          disabled={!canNote}
                          onClick={() => setReplyDraft(draft)}
                        >
                          {t("ticket.ai.useDraft")}
                        </Button>
                      </div>
                    </div>
                    <p
                      className={cn(
                        "whitespace-pre-wrap text-[13px] text-ink",
                        !expanded && "line-clamp-2",
                      )}
                      data-testid={`ai-panel-draft-body-${draft.id}`}
                    >
                      {draft.body}
                    </p>
                    <div className="flex items-center gap-3">
                      <button
                        type="button"
                        className="text-[11px] font-medium text-accent hover:underline"
                        data-testid={`ai-panel-draft-toggle-${draft.id}`}
                        onClick={() => setExpandedDraftId(expanded ? null : draft.id)}
                      >
                        {expanded ? t("ticket.ai.collapse") : t("ticket.ai.expand")}
                      </button>
                      {draft.tool_trace?.length > 0 && (
                        <button
                          type="button"
                          className="text-[11px] font-medium text-muted hover:text-ink hover:underline"
                          data-testid={`ai-panel-draft-trace-toggle-${draft.id}`}
                          aria-expanded={openTraceId === draft.id}
                          onClick={() =>
                            setOpenTraceId(openTraceId === draft.id ? null : draft.id)
                          }
                        >
                          {t("ticket.ai.toolTrace", { count: draft.tool_trace.length })}
                          <span aria-hidden> {openTraceId === draft.id ? "▾" : "▸"}</span>
                        </button>
                      )}
                    </div>
                    {openTraceId === draft.id && (
                      <ul
                        className="space-y-1.5 border-l-2 border-hairline pl-2.5"
                        data-testid={`ai-panel-draft-trace-${draft.id}`}
                      >
                        {draft.tool_trace.map((step, i) => (
                          <li key={i} className="space-y-0.5">
                            <div className="font-mono text-[11px] font-medium text-muted">
                              {step.name}
                            </div>
                            <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded bg-surface p-1.5 font-mono text-[11px] leading-snug text-ink/80">
                              {step.content}
                            </pre>
                          </li>
                        ))}
                      </ul>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
          {discardMutation.isError && (
            <p className="text-xs text-danger" data-testid="ai-panel-discard-error">
              {mapRunError(discardMutation.error)}
            </p>
          )}
        </div>
      )}

      {replyDraft && replyArticleId != null && (
        <ReplyDialog
          ticketId={ticketId}
          articleId={replyArticleId}
          replyAll={false}
          open
          onClose={() => setReplyDraft(null)}
          initialDraft={{ id: replyDraft.id, subject: replyDraft.subject, body: replyDraft.body }}
        />
      )}

      {confirmDialog}
    </div>
  );
}
