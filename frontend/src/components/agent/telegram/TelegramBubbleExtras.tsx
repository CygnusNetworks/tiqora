import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { ArticleListItem, TelegramButtonIn, TelegramMessageMeta } from "@/lib/api";
import { senderDisplayName } from "@/lib/articleChannel";
import { cn } from "@/lib/cn";
import { Button } from "@/components/ui/Button";
import { useArticlePlainText } from "./useTelegramMessage";

/** Telegram's own text-message limit — the edit can't exceed what was sendable. */
const TELEGRAM_TEXT_MAX = 4096;
const QUOTE_CHARS = 80;

/** Reply context on top of a bubble, like Telegram's own quote strip. Clicking
 * it jumps to the quoted bubble. */
export function TelegramQuote({
  ticketId,
  quotedId,
  quoted,
}: {
  ticketId: number;
  quotedId: number;
  /** The quoted article when it's in the loaded list — absent for a message
   * outside the current filter; the snippet still loads from its body. */
  quoted: ArticleListItem | undefined;
}) {
  const { t } = useTranslation();
  const { plain } = useArticlePlainText(ticketId, quotedId);
  const name = senderDisplayName(quoted?.from_address) || t("ticket.unknownSender");
  const snippet =
    plain === null ? null : plain.length > QUOTE_CHARS ? `${plain.slice(0, QUOTE_CHARS)}…` : plain;

  return (
    <button
      type="button"
      data-testid={`telegram-quote-${quotedId}`}
      onClick={() =>
        document
          .querySelector(`[data-testid="conversation-bubble-${quotedId}"]`)
          ?.scrollIntoView?.({ block: "center", behavior: "smooth" })
      }
      className="block w-full rounded-md border-l-2 border-accent bg-surface/60 px-2 py-1 text-left text-xs hover:bg-surface"
    >
      <span className="block font-semibold text-accent">{t("ticket.telegram.replyTo", { name })}</span>
      {snippet !== null && (
        <span className="block truncate text-muted" data-testid="telegram-quote-snippet">
          {snippet}
        </span>
      )}
    </button>
  );
}

/** The inline keyboard the customer saw under an agent message. Display only —
 * the agent can't press the customer's buttons. */
export function TelegramButtonPills({
  buttons,
  answered,
}: {
  buttons: TelegramButtonIn[];
  answered: number | null;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-wrap gap-1.5" aria-label={t("ticket.telegram.buttons")}>
      {buttons.map((b, i) => {
        const isAnswer = answered === i;
        return (
          <span
            key={i}
            data-testid={`telegram-button-${i}`}
            data-answered={isAnswer ? "true" : undefined}
            title={isAnswer ? t("ticket.telegram.answered") : undefined}
            className={cn(
              "rounded-full border px-2.5 py-0.5 text-xs",
              isAnswer
                ? "border-accent bg-accent text-accent-ink"
                : answered !== null
                  ? "border-hairline text-muted opacity-60"
                  : "border-accent/35 text-accent",
            )}
          >
            {b.label}
          </span>
        );
      })}
    </div>
  );
}

/** Footer line inside the bubble: edited/retracted state and the delivery
 * tick. Telegram's Bot API reports no read receipts, so there is only ever
 * the single "delivered" tick — never a double one. */
export function TelegramStatusLine({ meta }: { meta: TelegramMessageMeta }) {
  const { t } = useTranslation();
  const delivered = meta.direction === "out" && !meta.retracted_at;
  if (!meta.edited_at && !meta.retracted_at && !delivered) return null;
  return (
    <div className="flex items-center justify-end gap-1.5 text-[10px] text-muted">
      {meta.edited_at && <span>{t("ticket.telegram.edited")}</span>}
      {meta.retracted_at && (
        <span className="font-medium text-danger">{t("ticket.telegram.retracted")}</span>
      )}
      {delivered && (
        <span
          className="text-accent"
          data-testid="telegram-delivered"
          title={t("ticket.telegram.delivered")}
          aria-label={t("ticket.telegram.delivered")}
        >
          ✓
        </span>
      )}
    </div>
  );
}

/** Buttons for the bubble's hover action island. */
export function TelegramBubbleActions({
  articleId,
  canModify,
  onQuote,
  onEdit,
  onRetract,
}: {
  articleId: number;
  /** Own delivered message that hasn't been retracted. */
  canModify: boolean;
  onQuote: () => void;
  onEdit: () => void;
  onRetract: () => void;
}) {
  const { t } = useTranslation();
  // cn() is plain clsx (no tailwind-merge), so the hover colour is chosen
  // here rather than overridden per button.
  const btn = (hover: string) =>
    cn(
      "inline-flex h-6 min-w-6 items-center justify-center rounded px-1 text-xs text-muted hover:bg-surface-subtle",
      hover,
    );
  return (
    <div className="flex items-center gap-0.5" data-testid={`telegram-actions-${articleId}`}>
      <button
        type="button"
        className={btn("hover:text-ink")}
        title={t("ticket.telegram.quote")}
        aria-label={t("ticket.telegram.quote")}
        onClick={onQuote}
      >
        ❝
      </button>
      {canModify && (
        <>
          <button
            type="button"
            className={btn("hover:text-ink")}
            title={t("ticket.telegram.edit")}
            aria-label={t("ticket.telegram.edit")}
            onClick={onEdit}
          >
            ✎
          </button>
          <button
            type="button"
            className={btn("hover:text-danger")}
            title={t("ticket.telegram.retract")}
            aria-label={t("ticket.telegram.retract")}
            onClick={onRetract}
          >
            ⌫
          </button>
        </>
      )}
    </div>
  );
}

/** In-bubble editor replacing the body while editing. */
export function TelegramEditForm({
  initial,
  pending,
  onSave,
  onCancel,
}: {
  initial: string;
  pending: boolean;
  onSave: (body: string) => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const [value, setValue] = useState(initial);
  const unchanged = value.trim() === "" || value === initial;
  return (
    <div className="space-y-1.5">
      <textarea
        value={value}
        onChange={(e) => setValue(e.target.value)}
        maxLength={TELEGRAM_TEXT_MAX}
        rows={Math.min(8, Math.max(2, value.split("\n").length))}
        aria-label={t("ticket.telegram.editLabel")}
        autoFocus
        onKeyDown={(e) => {
          if (e.key === "Escape") onCancel();
        }}
        className="w-full min-w-[16rem] resize-y rounded-md border border-hairline bg-surface px-2 py-1 text-sm text-ink focus:border-accent focus:outline-none"
      />
      <div className="flex justify-end gap-1.5">
        <Button size="sm" variant="ghost" onClick={onCancel} disabled={pending}>
          {t("ticket.telegram.cancel")}
        </Button>
        <Button size="sm" variant="primary" onClick={() => onSave(value)} disabled={unchanged || pending}>
          {t("ticket.telegram.save")}
        </Button>
      </div>
    </div>
  );
}

/** Inline confirmation instead of a `confirm()` dialog. */
export function TelegramRetractConfirm({
  pending,
  onConfirm,
  onCancel,
}: {
  pending: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-wrap items-center justify-end gap-1.5 text-xs">
      <span className="font-medium text-danger">{t("ticket.telegram.retractConfirm")}</span>
      <Button size="sm" variant="danger" onClick={onConfirm} disabled={pending}>
        {t("ticket.telegram.yes")}
      </Button>
      <Button size="sm" variant="ghost" onClick={onCancel} disabled={pending}>
        {t("ticket.telegram.cancel")}
      </Button>
    </div>
  );
}
