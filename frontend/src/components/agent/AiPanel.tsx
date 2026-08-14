import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, ApiError } from "@/lib/api";
import { aiApi } from "@/lib/aiApi";
import { useAuth } from "@/auth/AuthContext";
import {
  ticketAiApi,
  type AiDraftOut,
  type SummaryDetail,
} from "@/lib/ticketAiApi";
import { articleSortKey } from "@/lib/article";
import { channelNameOf } from "@/lib/articleChannel";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/cn";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Spinner } from "@/components/ui/Spinner";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { Menu, MenuItem } from "@/components/ui/Menu";
import { HelpPopover } from "@/components/ui/HelpPopover";
import { ToolTraceCard } from "@/components/ai/ToolResultView";
import { ReplyDialog } from "./ReplyDialog";
import { SummaryText } from "./SummaryText";

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

const SUMMARY_DETAILS: SummaryDetail[] = ["standard", "detailed"];

/** Manual Assist draft POST returns immediately (nginx-90s-timeout fix) and
 * this panel polls `GET /tickets/{id}/ai` for the background run's outcome
 * at this interval while `manual_run_status === "running"`. */
const MANUAL_RUN_POLL_INTERVAL_MS = 2500;

/** Error codes both `_map_run_error`'s ApiError detail prefix (synchronous
 * failures, e.g. the lock check) and `manual_run_error_code` (background-run
 * outcome polled via GET) can carry — see backend `_run_error_code`. */
const MANUAL_RUN_MAPPED_ERROR_CODES = new Set([
  "llm_empty_output",
  "llm_timeout",
  "llm_provider_error",
  "ai_run_locked",
]);

type TFn = ReturnType<typeof useTranslation>["t"];

/** Single source of truth for the error-code → i18n text mapping, used both
 * for a synchronous request failure (ApiError detail prefix) and for a
 * background run's `manual_run_error_code` polled via GET — see
 * `mapRunError` and the `manual_run_status === "error"` branch below. */
function runErrorCodeText(t: TFn, code: string | undefined): string {
  switch (code) {
    case "llm_empty_output":
      return t("ticket.ai.errorLlmEmptyOutput");
    case "llm_timeout":
      return t("ticket.ai.errorLlmTimeout");
    case "llm_provider_error":
      return t("ticket.ai.errorLlmProviderError");
    case "ai_run_locked":
      return t("ticket.ai.errorLocked");
    default:
      return t("ticket.ai.errorGeneric");
  }
}

/** Extracts the stable error code from an ApiError's FastAPI `detail`
 * string, when it follows the `"<code>: <message>"` convention used by
 * `_map_run_error` (backend: api/v1/ai.py) for a few distinguishable
 * run/LLM failures. Returns `undefined` for plain-string details without
 * that prefix (e.g. "Manual Assist is disabled for this queue") — those
 * fall back to the generic per-status-code text. */
function errorDetailCode(error: ApiError): string | undefined {
  const payload = error.detail;
  const raw =
    payload && typeof payload === "object" && "detail" in payload
      ? (payload as { detail: unknown }).detail
      : payload;
  if (typeof raw !== "string") return undefined;
  const colonIdx = raw.indexOf(":");
  if (colonIdx <= 0) return undefined;
  return raw.slice(0, colonIdx).trim();
}

/** Coverage of the current summary over the ticket's articles: filled dots
 * are summarized, the outline dots arrived later. */
function CoverageDots({ covered, total }: { covered: number; total: number }) {
  if (total === 0) return null;
  if (total > MAX_COVERAGE_DOTS) {
    return (
      <span
        className="text-[11px] tabular-nums text-muted"
        data-testid="ai-summary-coverage"
      >
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
        clarify
          ? "bg-escalation/15 text-escalation"
          : "bg-accent-dim text-accent",
      )}
    >
      {clarify ? "?" : "↩"}
    </span>
  );
}

export function AiPanel({
  ticketId,
  canNote,
}: {
  ticketId: number;
  canNote: boolean;
}) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const isAdmin = Boolean(user?.is_admin);
  const { confirm, dialog: confirmDialog } = useConfirm();
  const [expandedDraftId, setExpandedDraftId] = useState<number | null>(null);
  const [openTraceId, setOpenTraceId] = useState<number | null>(null);
  const [replyDraft, setReplyDraft] = useState<AiDraftOut | null>(null);
  const [summaryDetail, setSummaryDetail] = useState<SummaryDetail>("standard");
  // Manual Assist background-run tracking (nginx-90s-timeout fix): the
  // draft POST returns "started" immediately, so the actual outcome is
  // polled via GET below. `myRunStartedAt` remembers the `manual_run_started_at`
  // this panel instance's OWN run reported, once seen — the skipped/error
  // result box only ever renders for a run that matches it, so reopening a
  // ticket with an old failed run sitting in the DB never surprises the
  // agent with a stale error (see the render guards below).
  const [myRunStartedAt, setMyRunStartedAt] = useState<string | null>(null);
  const [awaitingRunMarker, setAwaitingRunMarker] = useState(false);

  const stateQ = useQuery({
    queryKey: ["tickets", ticketId, "ai"],
    queryFn: ({ signal }) => ticketAiApi.getState(ticketId, signal),
    refetchInterval: (query) =>
      query.state.data?.manual_run_status === "running"
        ? MANUAL_RUN_POLL_INTERVAL_MS
        : false,
  });

  // Once the poll first observes "running" after a POST this panel just
  // triggered, capture its started_at as our own run's marker.
  useEffect(() => {
    if (
      awaitingRunMarker &&
      stateQ.data?.manual_run_status === "running" &&
      stateQ.data.manual_run_started_at
    ) {
      setMyRunStartedAt(stateQ.data.manual_run_started_at);
      setAwaitingRunMarker(false);
    }
  }, [
    awaitingRunMarker,
    stateQ.data?.manual_run_status,
    stateQ.data?.manual_run_started_at,
  ]);

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
      void queryClient.invalidateQueries({
        queryKey: ["tickets", ticketId, "ai"],
      });
    },
    onError: () => {
      setAwaitingRunMarker(false);
    },
  });

  const summarizeMutation = useMutation({
    mutationFn: (detail: SummaryDetail) =>
      ticketAiApi.summarize(ticketId, detail),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["tickets", ticketId, "ai"],
      });
    },
  });

  const discardMutation = useMutation({
    mutationFn: (draftId: number) =>
      ticketAiApi.discardDraft(ticketId, draftId),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["tickets", ticketId, "ai"],
      });
    },
  });

  const adminDeleteMutation = useMutation({
    mutationFn: (draftId: number) => aiApi.adminDeleteDraft(draftId),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["tickets", ticketId, "ai"],
      });
    },
  });

  const adminDeleteSummaryMutation = useMutation({
    mutationFn: () => aiApi.adminDeleteSummary(ticketId),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["tickets", ticketId, "ai"],
      });
    },
  });

  if (stateQ.isLoading || stateQ.isError || !stateQ.data) return null;

  const state = stateQ.data;
  if (!state.manual_assist_available && !state.summary_available) return null;

  // Only this panel instance's OWN triggered run ever renders a
  // running/skipped/error result — see the `myRunStartedAt` doc comment
  // above. A run in progress before this comparison even resolves (the
  // brief window between the POST resolving and the first poll observing
  // "running") is covered by `awaitingRunMarker` in the disabled/spinner
  // checks below.
  const isMyRun =
    myRunStartedAt != null && state.manual_run_started_at === myRunStartedAt;
  // A run in "running" state is CURRENT, not stale — show the spinner text
  // and block the button for every session (also after a page reload),
  // regardless of who started it. Only the terminal skipped/error results
  // stay gated to the starting panel instance via `isMyRun`.
  const manualRunActive = state.manual_run_status === "running";
  const manualRunSkipped =
    isMyRun &&
    (state.manual_run_status === "skipped" ||
      state.manual_run_status === "escalated" ||
      state.manual_run_status === "superseded");
  const manualRunErrored = isMyRun && state.manual_run_status === "error";
  const manualRunBusy =
    draftMutation.isPending || awaitingRunMarker || manualRunActive;

  const openDrafts = state.drafts.filter((d) => d.status === "open");
  // Admins additionally see non-open drafts (accepted/discarded/superseded)
  // so they can hard-delete them — the reason the delete option was
  // previously "invisible" for old drafts.
  const visibleDrafts = isAdmin ? state.drafts : openDrafts;
  const locale = i18n.language;

  const mapRunError = (error: unknown): string => {
    if (error instanceof ApiError) {
      // Stable "<code>: <message>" detail prefix (backend: _map_run_error in
      // api/v1/ai.py) for the cases with a specific, actionable message —
      // matched before the generic status-code fallbacks below so a known
      // code always wins even if the status code is shared with other
      // (unrelated) 409s.
      const code = errorDetailCode(error);
      if (code && MANUAL_RUN_MAPPED_ERROR_CODES.has(code)) {
        return runErrorCodeText(t, code);
      }
      if (error.status === 423) return t("ticket.ai.errorLocked");
      if (error.status === 403) return t("ticket.ai.errorForbidden");
      if (error.status === 429) return t("ticket.ai.errorRateLimited");
      if (error.status === 409)
        return error.message || t("ticket.ai.errorDisabled");
    }
    return t("ticket.ai.errorGeneric");
  };

  const articles = articlesQ.data ?? [];
  const upto = state.last_summary_upto_article_id;
  const coveredCount =
    upto == null ? 0 : articles.filter((a) => a.id <= upto).length;
  const staleCount =
    upto == null ? 0 : articles.filter((a) => a.id > upto).length;
  const hasSummary = state.summary_body != null;

  const replyArticleId =
    replyDraft?.based_on_article_id ??
    [...articles].sort((a, b) => articleSortKey(b) - articleSortKey(a))[0]
      ?.id ??
    null;
  const replyArticle =
    replyArticleId != null ? articles.find((a) => a.id === replyArticleId) : undefined;

  return (
    <div
      className="space-y-3 rounded-lg border border-hairline bg-surface p-4"
      data-testid="ai-panel"
    >
      <h2 className="font-display text-sm font-semibold text-ink">
        {t("ticket.ai.title")}
      </h2>

      {state.summary_available && (
        <div className="space-y-2" data-testid="ai-panel-summary">
          <div className="flex items-center justify-between gap-2">
            <span className="inline-flex items-center gap-1.5 text-xs uppercase tracking-wide text-muted">
              {t("ticket.ai.summaryLabel")}
              <HelpPopover
                title={t("ticket.ai.summaryLabel")}
                testId="ai-panel-help-summary"
              >
                {t("ticket.ai.help.summary")}
              </HelpPopover>
            </span>
            <div className="flex items-center gap-1.5">
              <div
                role="group"
                aria-label={t("ticket.ai.detailLabel")}
                className="inline-flex rounded-lg border border-hairline bg-surface p-0.5"
              >
                {SUMMARY_DETAILS.map((d) => (
                  <button
                    key={d}
                    type="button"
                    aria-pressed={summaryDetail === d}
                    data-testid={`ai-panel-summary-detail-${d}`}
                    disabled={
                      !state.can_summarize || summarizeMutation.isPending
                    }
                    onClick={() => setSummaryDetail(d)}
                    className={cn(
                      "rounded-md px-2.5 py-0.5 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
                      summaryDetail === d
                        ? "bg-accent text-accent-ink"
                        : "text-muted hover:bg-surface-subtle hover:text-ink",
                    )}
                  >
                    {t(`ticket.ai.detail.${d}`)}
                  </button>
                ))}
              </div>
              <Button
                size="sm"
                variant="primary"
                data-testid="ai-panel-summarize-button"
                disabled={!state.can_summarize || summarizeMutation.isPending}
                onClick={() => summarizeMutation.mutate(summaryDetail)}
              >
                {summarizeMutation.isPending ? (
                  <Spinner className="h-3.5 w-3.5" />
                ) : hasSummary ? (
                  t("ticket.ai.refreshButton")
                ) : (
                  t("ticket.ai.summarizeButton")
                )}
              </Button>
              {isAdmin && hasSummary && (
                <Menu
                  panelTestId="ai-panel-summary-menu"
                  trigger={({ ref, toggleProps }) => (
                    <button
                      type="button"
                      ref={ref}
                      {...toggleProps}
                      data-testid="ai-panel-summary-menu-trigger"
                      title={t("ticket.ai.moreActions")}
                      className="rounded-md px-1.5 py-1 text-sm leading-none text-muted transition-colors hover:bg-surface-subtle hover:text-ink"
                    >
                      ⋯
                    </button>
                  )}
                >
                  <MenuItem
                    danger
                    testId="ai-panel-summary-admin-delete"
                    onSelect={() => {
                      void (async () => {
                        const ok = await confirm({
                          title: t("ticket.ai.adminDeleteSummary"),
                          message: t("ticket.ai.adminDeleteSummaryConfirm"),
                          variant: "danger",
                        });
                        if (ok) adminDeleteSummaryMutation.mutate();
                      })();
                    }}
                  >
                    {t("ticket.ai.adminDeleteSummary")}
                  </MenuItem>
                </Menu>
              )}
            </div>
          </div>
          {adminDeleteSummaryMutation.isError && (
            <p
              className="text-xs text-danger"
              data-testid="ai-panel-summary-admin-delete-error"
            >
              {mapRunError(adminDeleteSummaryMutation.error)}
            </p>
          )}

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
                  <CoverageDots
                    covered={coveredCount}
                    total={articles.length}
                  />
                )}
                {state.summary_created_at && (
                  <span
                    className="text-[11px] text-muted"
                    data-testid="ai-summary-created-at"
                  >
                    {t("ticket.ai.summaryCreatedAt", {
                      dateTime: formatDateTime(
                        state.summary_created_at,
                        locale,
                      ),
                    })}
                  </span>
                )}
              </div>
              <SummaryText
                body={state.summary_body ?? ""}
                testId="ai-panel-summary-body"
              />
            </div>
          ) : (
            <p
              className="text-sm text-muted"
              data-testid="ai-panel-summary-empty"
            >
              {t("ticket.ai.summaryEmpty")}
            </p>
          )}
          {summarizeMutation.isSuccess &&
            summarizeMutation.data.status === "up_to_date" && (
              <p
                className="text-xs text-muted"
                data-testid="ai-panel-summary-uptodate"
              >
                {t("ticket.ai.summaryUpToDate")}
              </p>
            )}
          {summarizeMutation.isError && (
            <p
              className="text-xs text-danger"
              data-testid="ai-panel-summary-error"
            >
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
              <HelpPopover
                title={t("ticket.ai.draftsLabel")}
                testId="ai-panel-help-drafts"
              >
                {t("ticket.ai.help.drafts")}
              </HelpPopover>
            </span>
            <span
              title={!canNote ? t("ticket.toolbar.noPermission") : undefined}
            >
              <Button
                size="sm"
                variant="primary"
                data-testid="ai-panel-create-draft-button"
                disabled={!canNote || manualRunBusy}
                onClick={() => {
                  setMyRunStartedAt(null);
                  setAwaitingRunMarker(true);
                  draftMutation.mutate();
                }}
              >
                {manualRunBusy ? (
                  <Spinner className="h-3.5 w-3.5" />
                ) : (
                  t("ticket.ai.createDraftButton")
                )}
              </Button>
            </span>
          </div>
          {manualRunBusy && (
            <p className="text-xs text-muted" data-testid="ai-panel-draft-running">
              {manualRunActive
                ? t("ticket.ai.draftRunning")
                : t("ticket.ai.createDraftHint")}
            </p>
          )}
          {draftMutation.isError && (
            <p
              className="text-xs text-danger"
              data-testid="ai-panel-draft-error"
            >
              {mapRunError(draftMutation.error)}
            </p>
          )}
          {manualRunSkipped && (
            <div
              className="rounded-md border border-hairline bg-surface-subtle p-2 text-xs text-muted"
              data-testid="ai-panel-draft-skipped"
            >
              <p>{t("ticket.ai.draftSkipped")}</p>
              {state.manual_run_notes && (
                <p
                  className="mt-1 text-[11px] text-muted/80"
                  data-testid="ai-panel-draft-skipped-notes"
                >
                  {state.manual_run_notes}
                </p>
              )}
            </div>
          )}
          {manualRunErrored && (
            <p
              className="text-xs text-danger"
              data-testid="ai-panel-draft-run-error"
            >
              {runErrorCodeText(t, state.manual_run_error_code ?? undefined)}
            </p>
          )}

          {visibleDrafts.length === 0 ? (
            <p
              className="text-sm text-muted"
              data-testid="ai-panel-drafts-empty"
            >
              {t("ticket.ai.draftsEmpty")}
            </p>
          ) : (
            <ul className="space-y-2">
              {visibleDrafts.map((draft) => {
                const expanded = expandedDraftId === draft.id;
                const isOpen = draft.status === "open";
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
                              defaultValue: t(
                                `ticket.ai.draftKind.${draft.kind}`,
                                {
                                  defaultValue: draft.kind,
                                },
                              ),
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
                        {!isOpen && (
                          <Badge
                            tone="muted"
                            data-testid={`ai-panel-draft-status-${draft.id}`}
                          >
                            {t(`ticket.ai.draftStatus.${draft.status}`, {
                              defaultValue: draft.status,
                            })}
                          </Badge>
                        )}
                        {isOpen && (
                          <>
                            <Button
                              size="sm"
                              variant="secondary"
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
                              {discardMutation.isPending &&
                              discardMutation.variables === draft.id ? (
                                <Spinner className="h-3.5 w-3.5" />
                              ) : (
                                t("ticket.ai.discardDraft")
                              )}
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
                          </>
                        )}
                        {isAdmin && (
                          <Menu
                            panelTestId={`ai-panel-draft-menu-${draft.id}`}
                            trigger={({ ref, toggleProps }) => (
                              <button
                                type="button"
                                ref={ref}
                                {...toggleProps}
                                data-testid={`ai-panel-draft-menu-trigger-${draft.id}`}
                                title={t("ticket.ai.moreActions")}
                                className="rounded-md px-1.5 py-1 text-sm leading-none text-muted transition-colors hover:bg-surface hover:text-ink"
                              >
                                ⋯
                              </button>
                            )}
                          >
                            <MenuItem
                              danger
                              testId={`ai-panel-draft-admin-delete-${draft.id}`}
                              onSelect={() => {
                                void (async () => {
                                  const ok = await confirm({
                                    title: t("ticket.ai.adminDeleteDraft"),
                                    message: t("ticket.ai.adminDeleteConfirm"),
                                    variant: "danger",
                                  });
                                  if (ok) adminDeleteMutation.mutate(draft.id);
                                })();
                              }}
                            >
                              {t("ticket.ai.adminDeleteDraft")}
                            </MenuItem>
                          </Menu>
                        )}
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
                        onClick={() =>
                          setExpandedDraftId(expanded ? null : draft.id)
                        }
                      >
                        {expanded
                          ? t("ticket.ai.collapse")
                          : t("ticket.ai.expand")}
                      </button>
                      {isAdmin && draft.tool_trace?.length > 0 && (
                        <button
                          type="button"
                          className="text-[11px] font-medium text-muted hover:text-ink hover:underline"
                          data-testid={`ai-panel-draft-trace-toggle-${draft.id}`}
                          aria-expanded={openTraceId === draft.id}
                          onClick={() =>
                            setOpenTraceId(
                              openTraceId === draft.id ? null : draft.id,
                            )
                          }
                        >
                          {t("ticket.ai.toolTrace", {
                            count: draft.tool_trace.length,
                          })}
                          <span aria-hidden>
                            {" "}
                            {openTraceId === draft.id ? "▾" : "▸"}
                          </span>
                        </button>
                      )}
                    </div>
                    {isAdmin && openTraceId === draft.id && (
                      <ul
                        className="space-y-1.5"
                        data-testid={`ai-panel-draft-trace-${draft.id}`}
                      >
                        {draft.tool_trace.map((step, i) => (
                          <li key={i}>
                            <ToolTraceCard
                              name={step.name}
                              content={step.content}
                              testId={`ai-panel-draft-trace-step-${draft.id}-${i}`}
                            />
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
            <p
              className="text-xs text-danger"
              data-testid="ai-panel-discard-error"
            >
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
          initialDraft={{
            id: replyDraft.id,
            subject: replyDraft.subject,
            body: replyDraft.body,
          }}
          channelName={replyArticle ? channelNameOf(replyArticle) : undefined}
        />
      )}

      {confirmDialog}
    </div>
  );
}
