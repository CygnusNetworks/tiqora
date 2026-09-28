import { useTranslation } from "react-i18next";
import { cn } from "@/lib/cn";
import { todayIso, type NextState } from "../replyNextState";
import { MAX_MESSAGE_LENGTH } from "./chatComposerHelpers";

const NEXT_LABEL_KEY: Record<NextState, string> = {
  keep: "ticket.telegram.composer.nextKeep",
  pending: "ticket.telegram.composer.nextPending",
  closed: "ticket.telegram.composer.nextClosed",
};

const kbdCls =
  "rounded border border-b-2 border-hairline bg-surface-subtle px-1 font-mono text-[10.5px] text-ink";

function pillCls(on: boolean) {
  return cn(
    "rounded-full border px-2 py-px",
    on ? "border-accent/50 bg-accent-dim text-accent" : "border-hairline hover:text-ink",
  );
}

/** Shortcut hints, the "Buttons" toggle, "danach …" and the length counter. */
export function ComposerFooter({
  length,
  buttonsOn,
  onToggleButtons,
  nextOptions,
  nextState,
  onNextState,
  pendingDate,
  onPendingDate,
}: {
  length: number;
  buttonsOn: boolean;
  onToggleButtons: () => void;
  /** Empty when the agent may not change the state (no `rw`). */
  nextOptions: { key: NextState }[];
  nextState: NextState;
  onNextState: (next: NextState) => void;
  pendingDate: string;
  onPendingDate: (date: string) => void;
}) {
  const { t } = useTranslation();
  const tooLong = length > MAX_MESSAGE_LENGTH;
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5 text-[11.5px] text-muted">
      <span className="inline-flex flex-wrap items-center gap-1">
        <kbd className={kbdCls}>Enter</kbd> {t("ticket.telegram.composer.hintSend")} ·{" "}
        <kbd className={kbdCls}>⇧ Enter</kbd> {t("ticket.telegram.composer.hintNewline")} ·{" "}
        <kbd className={kbdCls}>/</kbd> {t("ticket.telegram.composer.hintSnippet")}
      </span>
      <span className="inline-flex flex-wrap items-center gap-1.5">
        <button
          type="button"
          data-testid="tg-composer-buttons-toggle"
          aria-pressed={buttonsOn}
          onClick={onToggleButtons}
          className={pillCls(buttonsOn)}
        >
          {t("ticket.telegram.composer.buttons")}
        </button>
        {nextOptions.length > 1 && (
          <span
            role="group"
            aria-label={t("ticket.telegram.composer.next")}
            className="inline-flex flex-wrap items-center gap-1"
          >
            {t("ticket.telegram.composer.next")}
            {nextOptions.map((o) => (
              <button
                key={o.key}
                type="button"
                data-testid={`tg-composer-next-${o.key}`}
                aria-pressed={nextState === o.key}
                onClick={() => onNextState(o.key)}
                className={pillCls(nextState === o.key)}
              >
                {t(NEXT_LABEL_KEY[o.key])}
              </button>
            ))}
            {nextState === "pending" && (
              <input
                type="date"
                value={pendingDate}
                min={todayIso()}
                onChange={(e) => onPendingDate(e.target.value)}
                aria-label={t("ticket.replyNext.until")}
                data-testid="tg-composer-pending-date"
                className="rounded border border-hairline bg-surface px-1 text-[11.5px] text-ink"
              />
            )}
          </span>
        )}
        <span
          data-testid="tg-composer-counter"
          className={cn("font-mono tabular-nums", tooLong && "font-semibold text-danger")}
        >
          {length} / {MAX_MESSAGE_LENGTH}
        </span>
      </span>
    </div>
  );
}
