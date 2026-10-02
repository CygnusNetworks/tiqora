import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/Button";
import { Popover } from "@/components/ui/Popover";
import { usePopoverClose } from "@/components/ui/popoverContext";
import { Spinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/cn";
import { formatDateTime } from "@/lib/format";
import {
  ticketAiApi,
  type AiAutopilotIn,
  type AiAutopilotOut,
} from "@/lib/ticketAiApi";

/** Release sizes offered when switching the autopilot back on. */
const RUN_CHOICES = [1, 3, 5, 10] as const;
const DEFAULT_RUNS = 3;

const KNOWN_REASONS = new Set([
  "max_clarifications",
  "max_auto_replies",
  "grant_used",
  "escalate_to_human",
  "identity",
]);

/**
 * The per-ticket AI autopilot switch in the ticket header: shows whether the
 * AI answers this ticket automatically (and how many replies of a manual
 * release are left), and lets an agent stop it or switch it back on for N
 * replies. Hidden when the ticket's queue has no auto-reply at all.
 *
 * Shares the `["tickets", id, "ai"]` query with `AiPanel`, so it costs no
 * extra request on the ticket page.
 */
export function AutopilotSwitch({ ticketId, canNote }: { ticketId: number; canNote: boolean }) {
  const { t, i18n } = useTranslation();
  const stateQ = useQuery({
    queryKey: ["tickets", ticketId, "ai"],
    queryFn: ({ signal }) => ticketAiApi.getState(ticketId, signal),
  });
  const auto = stateQ.data?.autopilot;
  if (!auto || auto.mode === "unavailable") return null;

  const locale = i18n.language;
  const isActive = auto.mode === "active";
  const isStopped = auto.mode === "stopped";
  const granted = isActive && auto.grant_remaining != null && auto.grant_total != null;

  const label = isActive
    ? t("ticket.autopilot.pill.active")
    : isStopped
      ? t("ticket.autopilot.pill.stopped")
      : t("ticket.autopilot.pill.handedOver");
  const sub = granted
    ? t("ticket.autopilot.sub.grant", {
        remaining: auto.grant_remaining,
        total: auto.grant_total,
      })
    : isActive
      ? t("ticket.autopilot.sub.queueLimits")
      : isStopped
        ? auto.by_name
          ? t("ticket.autopilot.sub.stoppedBy", { name: auto.by_name })
          : formatDateTime(auto.since, locale)
        : reasonText(t, auto.reason);

  return (
    <Popover
      align="right"
      label={t("ticket.autopilot.title")}
      panelTestId="autopilot-panel"
      panelClassName="w-80"
      trigger={({ ref, toggleProps }) => (
        <span title={!canNote ? t("ticket.toolbar.noPermission") : undefined} className="inline-flex">
          <button
            ref={ref}
            type="button"
            {...toggleProps}
            disabled={!canNote}
            data-testid="autopilot-switch"
            data-mode={auto.mode}
            className={cn(
              "inline-flex h-8 items-center gap-2 rounded-full border px-3 text-left transition-colors duration-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-70",
              isActive && "border-green/40 bg-green/10 hover:bg-green/15",
              isStopped && "border-hairline bg-surface-subtle hover:bg-surface",
              auto.mode === "handed_over" && "border-amber/45 bg-amber/10 hover:bg-amber/15",
            )}
          >
            <span
              aria-hidden
              className={cn(
                "h-2 w-2 shrink-0 rounded-full",
                isActive && "bg-green",
                isStopped && "bg-muted",
                auto.mode === "handed_over" && "bg-amber",
              )}
            />
            <span className="flex flex-col leading-tight">
              <span className="text-xs font-semibold text-ink">{label}</span>
              <span className="text-[10.5px] text-muted">{sub}</span>
            </span>
          </button>
        </span>
      )}
    >
      <AutopilotPanel ticketId={ticketId} auto={auto} />
    </Popover>
  );
}

function reasonText(
  t: (key: string, options?: Record<string, unknown>) => string,
  reason: string | null | undefined,
): string {
  return t(`ticket.autopilot.reason.${reason && KNOWN_REASONS.has(reason) ? reason : "unknown"}`);
}

function AutopilotPanel({ ticketId, auto }: { ticketId: number; auto: AiAutopilotOut }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const close = usePopoverClose();
  const [runs, setRuns] = useState<number>(DEFAULT_RUNS);
  const [answerNow, setAnswerNow] = useState(true);
  const isActive = auto.mode === "active";
  const canAnswerNow = Boolean(auto.unanswered_customer_message);

  // Starting or stopping writes an internal note, so refresh the whole
  // ticket (articles, history, AI state), not only the ai key.
  const mutation = useMutation({
    mutationFn: (body: AiAutopilotIn) => ticketAiApi.autopilot(ticketId, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["tickets", ticketId] });
      close();
    },
  });

  const explain = isActive
    ? t("ticket.autopilot.explain.active")
    : auto.mode === "stopped"
      ? t("ticket.autopilot.explain.stopped")
      : t("ticket.autopilot.explain.handedOver");

  return (
    <div className="space-y-3" data-testid="autopilot-body">
      <div className="space-y-1">
        <p className="text-sm font-semibold text-ink">{t("ticket.autopilot.title")}</p>
        <p className="text-xs leading-relaxed text-muted">{explain}</p>
      </div>

      {isActive ? (
        <div className="space-y-2.5">
          <div className="flex items-center justify-between rounded-md bg-surface-subtle px-2.5 py-2 text-xs">
            <span className="text-muted">{t("ticket.autopilot.remaining")}</span>
            <span className="font-mono text-ink" data-testid="autopilot-remaining">
              {auto.grant_remaining != null && auto.grant_total != null
                ? t("ticket.autopilot.remainingOf", {
                    remaining: auto.grant_remaining,
                    total: auto.grant_total,
                  })
                : t("ticket.autopilot.remainingQueue")}
            </span>
          </div>
          {/* A plain button: the Button variants set their own border and text
              colour, which `cn` would not override. */}
          <button
            type="button"
            className="inline-flex h-8 w-full items-center justify-center rounded-md border border-danger/40 bg-danger/10 text-sm font-medium text-danger transition-colors duration-100 hover:bg-danger/15 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent disabled:opacity-60"
            data-testid="autopilot-stop"
            disabled={mutation.isPending}
            onClick={() => mutation.mutate({ action: "stop" })}
          >
            {mutation.isPending ? <Spinner className="h-3.5 w-3.5" /> : t("ticket.autopilot.stop")}
          </button>
          <p className="text-[11px] leading-relaxed text-muted">{t("ticket.autopilot.stopHint")}</p>
        </div>
      ) : (
        <div className="space-y-2.5">
          <p className="text-xs font-medium text-ink">{t("ticket.autopilot.startFor")}</p>
          <div className="grid grid-cols-4 gap-1.5" role="group" aria-label={t("ticket.autopilot.startFor")}>
            {RUN_CHOICES.map((n) => (
              <button
                key={n}
                type="button"
                aria-pressed={runs === n}
                data-testid={`autopilot-runs-${n}`}
                onClick={() => setRuns(n)}
                className={cn(
                  "h-8 rounded-md border font-mono text-sm transition-colors duration-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
                  runs === n
                    ? "border-accent bg-accent-dim font-semibold text-ink"
                    : "border-hairline bg-surface text-muted hover:bg-surface-subtle hover:text-ink",
                )}
              >
                {n}
              </button>
            ))}
          </div>
          <p className="text-[11px] leading-relaxed text-muted">{t("ticket.autopilot.startUnit")}</p>
          {canAnswerNow && (
            <label className="flex cursor-pointer items-start gap-2 text-xs leading-snug text-ink">
              <input
                type="checkbox"
                checked={answerNow}
                onChange={(e) => setAnswerNow(e.target.checked)}
                data-testid="autopilot-answer-now"
                className="mt-0.5 h-3.5 w-3.5 accent-accent"
              />
              {t("ticket.autopilot.answerNow")}
            </label>
          )}
          <Button
            variant="primary"
            size="sm"
            className="w-full justify-center"
            data-testid="autopilot-start"
            disabled={mutation.isPending}
            onClick={() =>
              mutation.mutate({
                action: "start",
                runs,
                answer_latest: canAnswerNow && answerNow,
              })
            }
          >
            {mutation.isPending ? (
              <Spinner className="h-3.5 w-3.5" />
            ) : (
              t("ticket.autopilot.start", { count: runs })
            )}
          </Button>
          <p className="text-[11px] leading-relaxed text-muted">{t("ticket.autopilot.startHint")}</p>
        </div>
      )}

      {mutation.isError && (
        <p className="text-xs text-danger" role="alert" data-testid="autopilot-error">
          {t("ticket.autopilot.error")}
        </p>
      )}
    </div>
  );
}
