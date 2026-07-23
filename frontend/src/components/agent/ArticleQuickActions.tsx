import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { ArticleListItem } from "@/lib/api";
import { isInternalNote } from "@/lib/articleChannel";
import { Button } from "@/components/ui/Button";
import { Menu, MenuItem } from "@/components/ui/Menu";
import { ReplyDialog } from "./ReplyDialog";
import { BounceDialog, ForwardDialog, SplitDialog } from "./ArticleActionDialogs";
import { useDeleteArticleNote } from "./useDeleteArticleNote";

/**
 * Reply / reply-all / forward as buttons; the less-common bounce/split
 * actions live under a ⋮ menu (known Menu clipping bug rules out nesting a
 * second flyout, so this stays single-level). Same dialogs as the former
 * per-article `ArticleActions` row — shared by the split view's reading
 * pane and the conversation view's per-bubble hover action island.
 */
export function ArticleQuickActions({
  ticketId,
  article,
  canNote,
  canDelete = false,
  replyTestId,
  compact = false,
}: {
  ticketId: number;
  article: ArticleListItem;
  canNote: boolean;
  /** Whether the agent may delete this article (``rw`` permission). Delete
   * is only ever offered for internal, non-customer-visible notes. */
  canDelete?: boolean;
  /** Testid for the primary reply button (split pane needs a stable one). */
  replyTestId?: string;
  /** Icon-only buttons for the conversation view's hover island. */
  compact?: boolean;
}) {
  const { t } = useTranslation();
  const [dialog, setDialog] = useState<"reply" | "replyAll" | "forward" | "bounce" | "split" | null>(
    null,
  );
  const hasMultipleRecipients =
    (article.to_address ?? "").includes(",") || Boolean(article.to_address && article.from_address);
  const noPerm = t("ticket.toolbar.noPermission");
  const showDelete = canDelete && isInternalNote(article);
  const del = useDeleteArticleNote(ticketId, article.id);

  return (
    <div className="flex flex-wrap items-center gap-1.5" data-testid={`article-actions-${article.id}`}>
      <span title={!canNote ? noPerm : undefined} className="inline-flex">
        <Button
          size="sm"
          variant="primary"
          disabled={!canNote}
          data-testid={replyTestId}
          title={compact ? t("ticket.reply") : undefined}
          onClick={() => setDialog("reply")}
        >
          ↩ {!compact && t("ticket.reply")}
        </Button>
      </span>
      {hasMultipleRecipients && !compact && (
        <span title={!canNote ? noPerm : undefined} className="inline-flex">
          <Button size="sm" variant="secondary" disabled={!canNote} onClick={() => setDialog("replyAll")}>
            ↪ {t("ticket.replyAll")}
          </Button>
        </span>
      )}
      <Button
        size="sm"
        variant="secondary"
        title={compact ? t("ticket.forward") : undefined}
        onClick={() => setDialog("forward")}
      >
        → {!compact && t("ticket.forward")}
      </Button>
      <Menu
        align="right"
        trigger={({ ref, toggleProps }) => (
          <button
            ref={ref}
            type="button"
            aria-label={t("ticket.moreActions")}
            className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-hairline text-muted hover:bg-surface-subtle"
            {...toggleProps}
          >
            ⋮
          </button>
        )}
      >
        <MenuItem onSelect={() => setDialog("bounce")}>{t("ticket.bounce")}</MenuItem>
        <MenuItem onSelect={() => setDialog("split")}>{t("ticket.split")}</MenuItem>
        {showDelete && (
          <MenuItem
            danger
            testId={`article-delete-${article.id}`}
            onSelect={() => void del.requestDelete()}
          >
            {t("ticket.deleteNote")}
          </MenuItem>
        )}
      </Menu>
      {del.errorMessage && (
        <p className="text-xs text-danger" data-testid={`article-delete-error-${article.id}`}>
          {del.errorMessage}
        </p>
      )}
      {showDelete && del.dialog}

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
