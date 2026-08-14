import { useState } from "react";
import { useTranslation } from "react-i18next";
import { formatDateTime } from "@/lib/format";
import { draftPreview, useClearReplyDraft, type ReplyDraft } from "@/lib/replyDrafts";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { Button } from "@/components/ui/Button";
import { ReplyDialog } from "./ReplyDialog";

/**
 * Renderings of an unsent reply draft (see `@/lib/replyDrafts`) inside the
 * two article views. Both open the very composer the text came from, so
 * "continue" is a real continuation rather than a fresh reply.
 *
 * The visual language is deliberately NOT the dashed amber of an internal
 * note (`ArticleConversationView`'s `NotePill`): a draft uses a solid accent
 * border plus an accent spine, so the two never read as the same thing.
 */

/** Shared open/continue/discard plumbing for both renderings. */
function useDraftActions(draft: ReplyDraft, channelName?: string) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const { confirm, dialog: confirmDialog } = useConfirm();
  const clearDraft = useClearReplyDraft();

  const discard = async () => {
    const ok = await confirm({
      title: t("ticket.draftDiscard"),
      message: t("ticket.draftDiscardConfirm"),
      confirmLabel: t("ticket.draftDiscardConfirmButton"),
      variant: "danger",
    });
    if (!ok) return;
    clearDraft(draft.ticketId, draft.articleId);
  };

  const dialog = (
    <>
      {confirmDialog}
      <ReplyDialog
        ticketId={draft.ticketId}
        articleId={draft.articleId}
        replyAll={draft.replyAll}
        open={open}
        onClose={() => setOpen(false)}
        channelName={channelName}
      />
    </>
  );

  return { open: () => setOpen(true), discard, dialog };
}

/**
 * Split view: an extra row pinned to the top of the article list, above the
 * real articles regardless of sort direction — an unsent draft is always the
 * most current thing on the ticket.
 */
export function DraftListRow({
  draft,
  locale,
  channelName,
}: {
  draft: ReplyDraft;
  locale: string;
  /** Channel name of the article the draft replies to (see
   * `channelNameOf`/`dominantChannel` in `@/lib/articleChannel`) — routes
   * the reply dialog opened from this row. */
  channelName?: string;
}) {
  const { t } = useTranslation();
  const actions = useDraftActions(draft, channelName);
  const preview = draftPreview(draft.body, 80);

  return (
    <>
      <div
        role="option"
        aria-selected={false}
        data-testid={`article-draft-row-${draft.articleId}`}
        onClick={actions.open}
        className="flex cursor-pointer gap-2 rounded-md border-l-2 border-l-accent bg-accent-dim px-2 py-1.5"
      >
        <span
          aria-hidden
          className="mt-0.5 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent text-[11px] text-accent-ink"
        >
          ✎
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="min-w-0 flex-1 truncate text-xs font-semibold text-accent">
              {t("ticket.draftUnsent")}
            </span>
            <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted">
              {formatDateTime(new Date(draft.updatedAt), locale)}
            </span>
          </div>
          <p className="truncate text-[11px] text-muted">{preview || t("ticket.draftEmpty")}</p>
        </div>
      </div>
      {actions.dialog}
    </>
  );
}

/**
 * Conversation view: a bubble on the agent side, at the end of the thread —
 * where the reply will land once it is sent.
 */
export function DraftBubble({
  draft,
  locale,
  channelName,
}: {
  draft: ReplyDraft;
  locale: string;
  /** Channel name of the article the draft replies to (see
   * `channelNameOf`/`dominantChannel` in `@/lib/articleChannel`) — routes
   * the reply dialog opened from this bubble. */
  channelName?: string;
}) {
  const { t } = useTranslation();
  const actions = useDraftActions(draft, channelName);
  const preview = draftPreview(draft.body, 400);

  return (
    <>
      <div className="flex justify-end" data-testid={`conversation-draft-${draft.articleId}`}>
        <div className="flex max-w-[76%] flex-row-reverse items-start gap-2">
          <span
            aria-hidden
            className="mt-0.5 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent text-[11px] text-accent-ink"
          >
            ✎
          </span>
          <div className="min-w-0 space-y-1 rounded-2xl rounded-br-md border border-accent bg-surface px-3 py-2 shadow-[inset_3px_0_0_0_var(--color-accent)]">
            <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
              <span className="font-semibold text-accent">{t("ticket.draft")}</span>
              <span className="rounded-full bg-accent px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-accent-ink">
                {t("ticket.draftNotSent")}
              </span>
              <span className="font-mono tabular-nums text-muted">
                {t("ticket.draftEditedAt", {
                  time: formatDateTime(new Date(draft.updatedAt), locale),
                })}
              </span>
            </div>
            <p className="whitespace-pre-wrap font-mono text-[12.5px] leading-relaxed text-muted">
              {preview || t("ticket.draftEmpty")}
            </p>
            <div className="flex gap-1.5 pt-0.5">
              <Button
                size="sm"
                variant="primary"
                data-testid={`conversation-draft-edit-${draft.articleId}`}
                onClick={actions.open}
              >
                {t("ticket.draftEdit")}
              </Button>
              <Button size="sm" variant="secondary" onClick={() => void actions.discard()}>
                {t("ticket.draftDiscard")}
              </Button>
            </div>
          </div>
        </div>
      </div>
      {actions.dialog}
    </>
  );
}
