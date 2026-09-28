import { useTranslation } from "react-i18next";
import { useArticlePlainText } from "./useTelegramMessage";
import { formatBytes, type ChatAttachment } from "./useChatAttachments";

const chipCls =
  "inline-flex min-w-0 max-w-full items-center gap-1.5 rounded-full border border-hairline bg-surface-subtle py-0.5 pl-2 pr-0.5 text-xs";
const removeCls = "rounded-full px-1 text-muted hover:text-danger";

/** "Antwort auf: …" — the customer message the reply will quote in Telegram. */
export function QuoteChip({
  ticketId,
  articleId,
  onRemove,
}: {
  ticketId: number;
  articleId: number;
  onRemove: () => void;
}) {
  const { t } = useTranslation();
  const { plain } = useArticlePlainText(ticketId, articleId);
  const preview = plain ? plain.replace(/\s+/g, " ").trim() : "…";
  return (
    <span className={chipCls} data-testid="tg-composer-quote">
      <span className="shrink-0 font-semibold text-accent">{t("ticket.telegram.composer.quoteLabel")}:</span>
      <span className="min-w-0 truncate text-muted">{preview}</span>
      <button
        type="button"
        aria-label={t("ticket.telegram.composer.quoteRemove")}
        data-testid="tg-composer-quote-remove"
        onClick={onRemove}
        className={removeCls}
      >
        ×
      </button>
    </span>
  );
}

export function AttachmentChips({
  items,
  onRemove,
}: {
  items: ChatAttachment[];
  onRemove: (id: number) => void;
}) {
  const { t } = useTranslation();
  return (
    <>
      {items.map((a, i) => (
        <span key={a.id} className={chipCls} data-testid={`tg-composer-attachment-${i}`}>
          <span aria-hidden>📎</span>
          <span className="min-w-0 truncate text-ink">{a.name}</span>
          <span className="shrink-0 font-mono tabular-nums text-muted">
            {a.data === null ? t("ticket.telegram.composer.encoding") : formatBytes(a.size)}
          </span>
          <button
            type="button"
            aria-label={t("ticket.telegram.composer.attachmentRemove", { name: a.name })}
            data-testid={`tg-composer-attachment-remove-${i}`}
            onClick={() => onRemove(a.id)}
            className={removeCls}
          >
            ×
          </button>
        </span>
      ))}
    </>
  );
}
