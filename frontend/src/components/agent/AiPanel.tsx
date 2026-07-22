import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, ApiError } from "@/lib/api";
import { ticketAiApi, type AiDraftOut } from "@/lib/ticketAiApi";
import { articleSortKey } from "@/lib/article";
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
export function AiPanel({ ticketId, canNote }: { ticketId: number; canNote: boolean }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const [expandedDraftId, setExpandedDraftId] = useState<number | null>(null);
  const [replyDraft, setReplyDraft] = useState<AiDraftOut | null>(null);

  const stateQ = useQuery({
    queryKey: ["tickets", ticketId, "ai"],
    queryFn: ({ signal }) => ticketAiApi.getState(ticketId, signal),
  });

  // Fallback reply target when a draft has no based_on_article_id — shares
  // the query key with TicketHeaderActions/ArticleMasterDetail so this never
  // triggers a second request.
  const articlesQ = useQuery({
    queryKey: ["tickets", ticketId, "articles"],
    queryFn: () => api.listArticles(ticketId),
    enabled: Boolean(replyDraft) && replyDraft?.based_on_article_id == null,
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

  if (stateQ.isLoading || stateQ.isError || !stateQ.data) return null;

  const state = stateQ.data;
  if (!state.manual_assist_available && !state.summary_available) return null;

  const openDrafts = state.drafts.filter((d) => d.status === "open");

  const mapRunError = (error: unknown): string => {
    if (error instanceof ApiError) {
      if (error.status === 423) return t("ticket.ai.errorLocked");
      if (error.status === 403) return t("ticket.ai.errorForbidden");
      if (error.status === 429) return t("ticket.ai.errorRateLimited");
      if (error.status === 409) return error.message || t("ticket.ai.errorDisabled");
    }
    return t("ticket.ai.errorGeneric");
  };

  const replyArticleId =
    replyDraft?.based_on_article_id ??
    [...(articlesQ.data ?? [])].sort((a, b) => articleSortKey(b) - articleSortKey(a))[0]?.id ??
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
              {summarizeMutation.isPending ? <Spinner className="h-3.5 w-3.5" /> : t("ticket.ai.summarizeButton")}
            </Button>
          </div>
          {state.summary_body ? (
            <div>
              <p
                className="whitespace-pre-wrap text-sm text-ink"
                data-testid="ai-panel-summary-body"
              >
                {state.summary_body}
              </p>
              {state.last_summary_upto_article_id != null && (
                <p className="mt-1 text-[11px] text-muted">
                  {t("ticket.ai.summaryAsOf", { articleId: state.last_summary_upto_article_id })}
                </p>
              )}
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
                    className="space-y-1.5 rounded border border-hairline p-2"
                    data-testid={`ai-panel-draft-${draft.id}`}
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-1.5">
                        <Badge tone={draft.kind === "clarify" ? "warn" : "accent"}>
                          {t(`ticket.ai.draftKind.${draft.kind}`, { defaultValue: draft.kind })}
                        </Badge>
                        <Badge tone="muted">
                          {t(`ticket.ai.draftSource.${draft.source}`, { defaultValue: draft.source })}
                        </Badge>
                        {draft.based_on_article_id != null && (
                          <span className="text-[11px] text-muted">
                            {t("ticket.ai.basedOnArticle", { articleId: draft.based_on_article_id })}
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-1.5">
                        <Button
                          size="sm"
                          variant="ghost"
                          data-testid={`ai-panel-draft-toggle-${draft.id}`}
                          onClick={() => setExpandedDraftId(expanded ? null : draft.id)}
                        >
                          {expanded ? t("ticket.ai.collapse") : t("ticket.ai.expand")}
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
                      </div>
                    </div>
                    {expanded && (
                      <p
                        className="whitespace-pre-wrap text-sm text-ink"
                        data-testid={`ai-panel-draft-body-${draft.id}`}
                      >
                        {draft.body}
                      </p>
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
