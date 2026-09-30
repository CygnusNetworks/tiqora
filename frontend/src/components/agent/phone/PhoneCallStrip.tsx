import { useTranslation } from "react-i18next";
import { cn } from "@/lib/cn";
import { formatElapsed } from "@/lib/phoneCall";

export type CallDirection = "inbound" | "outbound";

function ArrowIn() {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden className="h-4 w-4 shrink-0">
      <path d="M12 4 4 12M4 6.5V12h5.5" />
    </svg>
  );
}

function ArrowOut() {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden className="h-4 w-4 shrink-0">
      <path d="M4 12 12 4M6.5 4H12v5.5" />
    </svg>
  );
}

const clock = (ms: number) =>
  new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

const segBtn =
  "inline-flex items-center gap-1.5 px-2.5 py-1 text-[12.5px] transition-colors duration-100 focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent";

/**
 * The call line at the top of a phone ticket (Decision 5 + 8): direction,
 * what the direction does, number, call times and the running call timer.
 * Opened from the call popup the direction is known and shown as a badge;
 * opened by hand it is a two-button toggle.
 */
export function PhoneCallStrip({
  direction,
  onDirectionChange,
  fixed,
  number,
  answeredAt,
  endedAt,
  elapsed,
  running,
  onToggleTimer,
}: {
  direction: CallDirection;
  onDirectionChange: (d: CallDirection) => void;
  /** Direction came from the call (popup): read-only badge. */
  fixed: boolean;
  number: string;
  /** Epoch ms the call was answered / ended (popup only). */
  answeredAt?: number;
  endedAt?: number;
  elapsed: number;
  running: boolean;
  onToggleTimer: () => void;
}) {
  const { t } = useTranslation();
  const inbound = direction === "inbound";
  const meta = [
    answeredAt !== undefined ? t("newTicket.call.answeredAt", { time: clock(answeredAt) }) : null,
    endedAt !== undefined ? t("newTicket.call.endedAt", { time: clock(endedAt) }) : null,
  ].filter(Boolean);

  return (
    <div
      className="-mx-4 -mt-4 mb-1 flex flex-wrap items-center gap-x-4 gap-y-2 rounded-t-xl border-b border-hairline bg-surface-subtle px-4 py-2.5"
      data-testid="phone-call-strip"
    >
      {fixed ? (
        <span
          data-testid="new-ticket-direction-badge"
          data-direction={direction}
          className={cn(
            "inline-flex items-center gap-[7px] rounded-full py-[3px] pl-[7px] pr-2.5 text-[13px] font-semibold",
            inbound ? "bg-green/15 text-green" : "bg-purple/15 text-purple",
          )}
        >
          {inbound ? <ArrowIn /> : <ArrowOut />}
          {inbound ? t("newTicket.call.badgeIn") : t("newTicket.call.badgeOut")}
        </span>
      ) : (
        <span
          role="group"
          aria-label={t("newTicket.directionLabel")}
          className="inline-flex overflow-hidden rounded-md border border-hairline bg-surface"
        >
          <button
            type="button"
            data-testid="new-ticket-direction-in"
            aria-pressed={inbound}
            onClick={() => onDirectionChange("inbound")}
            className={cn(segBtn, inbound ? "bg-green/15 font-semibold text-green" : "text-muted hover:text-ink")}
          >
            <ArrowIn />
            {t("newTicket.call.toggleIn")}
          </button>
          <button
            type="button"
            data-testid="new-ticket-direction-out"
            aria-pressed={!inbound}
            onClick={() => onDirectionChange("outbound")}
            className={cn(
              segBtn,
              "border-l border-hairline",
              !inbound ? "bg-purple/15 font-semibold text-purple" : "text-muted hover:text-ink",
            )}
          >
            <ArrowOut />
            {t("newTicket.call.toggleOut")}
          </button>
        </span>
      )}
      <span className="text-[12px] text-muted" data-testid="new-ticket-autoreply-hint">
        {inbound ? t("newTicket.call.autoReplyYes") : t("newTicket.call.autoReplyNo")}
      </span>
      {number.trim() !== "" && (
        <span className="font-mono text-[13px] text-ink" data-testid="phone-call-strip-number">
          {number}
        </span>
      )}
      {meta.length > 0 && (
        <span className="text-[12px] tabular-nums text-muted" data-testid="phone-call-strip-meta">
          {meta.join(" · ")}
        </span>
      )}
      <span className="ml-auto inline-flex items-center gap-2" title={t("phone.timer")}>
        <span aria-hidden className={cn("h-[7px] w-[7px] rounded-full", running ? "animate-pulse bg-danger" : "bg-muted")} />
        <span className="font-mono text-[12.5px] tabular-nums text-ink" data-testid="new-ticket-timer">
          {formatElapsed(elapsed)}
        </span>
        <button
          type="button"
          onClick={onToggleTimer}
          data-testid="new-ticket-timer-toggle"
          className="rounded px-1 py-0.5 text-[12.5px] text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
        >
          {running ? t("phone.pause") : t("phone.resume")}
        </button>
      </span>
    </div>
  );
}
