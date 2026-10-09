import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { Switch } from "@/components/ui/Switch";
import { apiErrorMessage } from "@/lib/avatarApi";
import { customerAiPauseApi, type CustomerAiPauseScope } from "@/lib/customerAiPauseApi";
import { cn } from "@/lib/cn";
import { formatDateTime } from "@/lib/format";

/** Durations offered in days; "until lifted" is the extra choice below them. */
const DAY_CHOICES = [1, 3, 7, 14, 30] as const;
export const DEFAULT_PAUSE_DAYS = 7;

const chipClass = (on: boolean) =>
  cn(
    "h-8 rounded-md border text-sm transition-colors duration-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
    on
      ? "border-accent bg-accent-dim font-semibold text-ink"
      : "border-hairline bg-surface text-muted hover:bg-surface-subtle hover:text-ink",
  );

/**
 * How long the AI automation stays off: a row of day counts plus "until
 * lifted" (`null`). Fits the narrow customer aside and the autopilot popover.
 * `data-testid={testId}` on the group, `${testId}-<days|forever>` per choice.
 */
export function PauseDurationPicker({
  value,
  onChange,
  testId,
}: {
  value: number | null;
  onChange: (days: number | null) => void;
  testId?: string;
}) {
  const { t } = useTranslation();
  return (
    <div className="space-y-1.5" role="group" aria-label={t("customerAiPause.duration")} data-testid={testId}>
      <p className="text-xs font-medium text-ink">{t("customerAiPause.durationDays")}</p>
      <div className="grid grid-cols-5 gap-1.5">
        {DAY_CHOICES.map((d) => (
          <button
            key={d}
            type="button"
            aria-pressed={value === d}
            aria-label={t("customerAiPause.days", { count: d })}
            data-testid={testId ? `${testId}-${d}` : undefined}
            onClick={() => onChange(d)}
            className={cn(chipClass(value === d), "font-mono")}
          >
            {d}
          </button>
        ))}
      </div>
      <button
        type="button"
        aria-pressed={value === null}
        data-testid={testId ? `${testId}-forever` : undefined}
        onClick={() => onChange(null)}
        className={cn(chipClass(value === null), "w-full")}
      >
        {t("customerAiPause.untilLifted")}
      </button>
    </div>
  );
}

/**
 * Switch the AI automation (auto-reply, triage, auto-summary) off for one
 * customer user or a whole company — for N days or until lifted. The switch
 * shows whether it runs; turning it off asks for how long (and why), turning
 * it back on lifts the pause. Everyone sees the state; agents who may edit
 * customers can change it. Manual Assist stays available either way.
 * `framed={false}` drops the own box, for a section of the customer inspector.
 */
export function CustomerAiPause({
  scope,
  customerKey,
  canEdit,
  framed = true,
}: {
  scope: CustomerAiPauseScope;
  customerKey: string;
  canEdit: boolean;
  framed?: boolean;
}) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const queryKey = ["customers", "ai-pause", scope, customerKey] as const;
  const [days, setDays] = useState<number | null>(DEFAULT_PAUSE_DAYS);
  const [reason, setReason] = useState("");
  const [formOpen, setFormOpen] = useState(false);

  const pauseQ = useQuery({
    queryKey,
    queryFn: ({ signal }) => customerAiPauseApi.get(scope, customerKey, signal),
    enabled: Boolean(customerKey),
  });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey });
    // The ticket headers show "paused for this customer".
    void queryClient.invalidateQueries({ queryKey: ["tickets"] });
  };
  const setMutation = useMutation({
    mutationFn: () => customerAiPauseApi.set(scope, customerKey, { days, reason: reason.trim() }),
    onSuccess: () => {
      setReason("");
      setFormOpen(false);
      refresh();
    },
  });
  const liftMutation = useMutation({
    mutationFn: () => customerAiPauseApi.lift(scope, customerKey),
    onSuccess: refresh,
  });

  const pause = pauseQ.data;
  if (pauseQ.isLoading) return <Spinner className="h-3.5 w-3.5" />;
  if (!pause) return null;
  // Nobody may change it and there is nothing to report.
  if (!pause.active && !canEdit) return null;

  const error = setMutation.error ?? liftMutation.error;
  const running = !pause.active && !formOpen;
  const busy = setMutation.isPending || liftMutation.isPending;
  const toggle = (on: boolean) => {
    if (pause.active) {
      if (on) liftMutation.mutate();
    } else {
      setFormOpen(!on);
    }
  };
  const titleId = `ai-pause-title-${scope}`;

  return (
    <section
      className={cn(
        "space-y-2.5 text-sm",
        framed && "rounded-lg border border-hairline bg-surface px-3.5 py-3",
      )}
      aria-labelledby={titleId}
      data-testid="customer-ai-pause"
      data-active={pause.active}
    >
      <div className="flex items-center gap-3">
        <h2 id={titleId} className="flex-1 text-sm font-semibold text-ink">
          {t("customerAiPause.title")}
        </h2>
        <Switch
          checked={running}
          onChange={toggle}
          disabled={!canEdit || busy}
          aria-label={t("customerAiPause.switchLabel")}
          testId="customer-ai-pause-switch"
        />
      </div>
      {pause.active ? (
        <div className="space-y-1">
          <p className="text-warn" data-testid="customer-ai-pause-state">
            {pause.paused_until
              ? t("customerAiPause.activeUntil", {
                  date: formatDateTime(pause.paused_until, i18n.language),
                })
              : t("customerAiPause.activeForever")}
          </p>
          {(pause.by_name || pause.reason) && (
            <p className="text-xs text-muted">
              {[
                pause.by_name ? t("customerAiPause.by", { name: pause.by_name }) : null,
                pause.reason,
              ]
                .filter(Boolean)
                .join(" — ")}
            </p>
          )}
        </div>
      ) : !formOpen ? (
        <p className="text-xs text-muted" data-testid="customer-ai-pause-state">
          {t("customerAiPause.off")}
        </p>
      ) : (
        <div className="space-y-2.5" data-testid="customer-ai-pause-form">
          <PauseDurationPicker value={days} onChange={setDays} testId="customer-ai-pause-days" />
          <input
            type="text"
            value={reason}
            maxLength={255}
            onChange={(e) => setReason(e.target.value)}
            placeholder={t("customerAiPause.reasonPlaceholder")}
            aria-label={t("customerAiPause.reason")}
            data-testid="customer-ai-pause-reason"
            className="h-8 w-full rounded-md border border-hairline bg-surface px-2.5 text-sm text-ink placeholder:text-muted focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/30"
          />
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setFormOpen(false)}
              disabled={setMutation.isPending}
            >
              {t("common.cancel")}
            </Button>
            <button
              type="button"
              className="inline-flex h-8 flex-1 items-center justify-center rounded-md border border-danger/40 bg-danger/10 px-2 text-sm font-medium text-danger transition-colors duration-100 hover:bg-danger/15 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent disabled:opacity-60"
              disabled={setMutation.isPending}
              onClick={() => setMutation.mutate()}
              data-testid="customer-ai-pause-set"
            >
              {setMutation.isPending ? (
                <Spinner className="h-3.5 w-3.5" />
              ) : days === null ? (
                t("customerAiPause.pauseForever")
              ) : (
                t("customerAiPause.pauseForDays", { count: days })
              )}
            </button>
          </div>
          <p className="text-[11px] leading-relaxed text-muted">{t("customerAiPause.hint")}</p>
        </div>
      )}
      {error && (
        <p className="text-xs text-danger" role="alert" data-testid="customer-ai-pause-error">
          {apiErrorMessage(error, t("customerAiPause.error"))}
        </p>
      )}
    </section>
  );
}
