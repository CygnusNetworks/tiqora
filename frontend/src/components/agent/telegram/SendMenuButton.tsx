import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/cn";
import { needsDate, todayIso, type NextState } from "../replyNextState";

const LABEL_KEY: Record<NextState, string> = {
  keep: "ticket.telegram.composer.sendKeep",
  pending: "ticket.telegram.composer.sendPending",
  autoclose: "ticket.telegram.composer.sendAutoClose",
  closed: "ticket.telegram.composer.sendClosed",
};
const HINT_KEY: Record<NextState, string> = {
  keep: "ticket.telegram.composer.sendKeepHint",
  pending: "ticket.telegram.composer.sendPendingHint",
  autoclose: "ticket.telegram.composer.sendAutoCloseHint",
  closed: "ticket.telegram.composer.sendClosedHint",
};
const IS_MAC = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);
/** Shown in the menu; the composer's textarea handles the actual keys
 * (Alt+Enter = pending, Cmd/Ctrl+Enter = closed). */
const SEND_SHORTCUT: Record<NextState, string | null> = {
  keep: "Enter",
  autoclose: null,
  pending: IS_MAC ? "⌥ Enter" : "Alt+Enter",
  closed: IS_MAC ? "⌘ Enter" : "Ctrl+Enter",
};

/** Plain Enter sends `primary`; "keep" then has no shortcut of its own. */
function shortcutFor(next: NextState, primary: NextState): string | null {
  if (next === primary) return "Enter";
  if (next === "keep") return null;
  return SEND_SHORTCUT[next];
}

/**
 * "Senden" plus a menu for what happens to the ticket afterwards. A plain
 * send leaves the ticket as it is (on a closed ticket: sets it to waiting); waiting and closing are explicit
 * menu choices instead of a sticky toggle that has to be reset. Without
 * permission to change the state (`options` only has "keep") there is no
 * menu at all.
 */
export function SendMenuButton({
  options,
  primary = "keep",
  disabled,
  sending,
  pendingDate,
  onPendingDate,
  onSend,
}: {
  options: NextState[];
  /** What the main button and plain Enter do (see `useNextStateOptions`'s
   * `defaultNext`): "keep", or "pending" on a closed ticket. */
  primary?: NextState;
  disabled: boolean;
  sending: boolean;
  pendingDate: string;
  onPendingDate: (date: string) => void;
  onSend: (next: NextState) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const hasMenu = options.length > 1;

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const pick = (next: NextState) => {
    setOpen(false);
    onSend(next);
  };
  const pendingInvalid = !pendingDate;

  return (
    <div ref={rootRef} className="relative flex h-8 shrink-0">
      <button
        type="button"
        data-testid="tg-composer-send"
        disabled={disabled || sending}
        onClick={() => onSend(primary)}
        className={cn(
          "bg-accent px-3 text-sm font-semibold text-accent-ink hover:bg-accent/90 disabled:opacity-50",
          hasMenu ? "rounded-l-lg" : "rounded-lg",
        )}
      >
        {sending
          ? t("ticket.telegram.composer.sending")
          : primary === "keep"
            ? t("ticket.telegram.composer.send")
            : t(LABEL_KEY[primary])}
      </button>
      {hasMenu && (
        <button
          type="button"
          data-testid="tg-composer-send-menu"
          aria-haspopup="menu"
          aria-expanded={open}
          aria-label={t("ticket.telegram.composer.sendOptions")}
          title={t("ticket.telegram.composer.sendOptions")}
          disabled={sending}
          onClick={() => setOpen((o) => !o)}
          className="rounded-r-lg border-l border-accent-ink/30 bg-accent px-2 text-xs text-accent-ink hover:bg-accent/90 disabled:opacity-50"
        >
          ▾
        </button>
      )}
      {open && (
        <div
          role="menu"
          data-testid="tg-composer-send-options"
          className="absolute bottom-full right-0 z-30 mb-1 grid w-72 gap-0.5 rounded-lg border border-hairline bg-surface-elevated p-1 shadow-lg"
        >
          {options.map((o) => (
            <div key={o} className="grid">
              <button
                type="button"
                role="menuitem"
                data-testid={`tg-composer-send-${o}`}
                disabled={disabled || (needsDate(o) && pendingInvalid)}
                onClick={() => pick(o)}
                className="grid grid-cols-[1fr_auto] gap-x-3 rounded-md px-2.5 py-1.5 text-left text-sm text-ink hover:bg-surface-subtle disabled:opacity-50"
              >
                <span>{t(LABEL_KEY[o])}</span>
                {shortcutFor(o, primary) ? (
                  <kbd className="self-center rounded border border-b-2 border-hairline bg-surface-subtle px-1 font-mono text-[10.5px] text-muted">
                    {shortcutFor(o, primary)}
                  </kbd>
                ) : (
                  <span />
                )}
                <span className="col-span-2 text-[11px] text-muted">{t(HINT_KEY[o])}</span>
              </button>
              {needsDate(o) && (
                <label className="flex items-center gap-2 px-2.5 pb-1.5 text-[11px] text-muted">
                  {t("ticket.replyNext.until")}
                  <input
                    type="date"
                    value={pendingDate}
                    min={todayIso()}
                    onChange={(e) => onPendingDate(e.target.value)}
                    data-testid="tg-composer-pending-date"
                    className="rounded border border-hairline bg-surface px-1 text-[11.5px] text-ink"
                  />
                </label>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
