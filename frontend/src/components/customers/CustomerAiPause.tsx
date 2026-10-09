import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { apiErrorMessage } from "@/lib/avatarApi";
import { customerAiPauseApi, type CustomerAiPauseScope } from "@/lib/customerAiPauseApi";
import { formatDateTime } from "@/lib/format";

/** Durations offered; `null` = until an agent lifts it. */
const DAY_CHOICES: (number | null)[] = [1, 3, 7, 14, 30, null];
const DEFAULT_DAYS = 7;

/**
 * Switch the AI automation (auto-reply, triage, auto-summary) off for one
 * customer user or a whole company — for N days or until lifted. Everyone sees
 * the state; agents who may edit customers can change it. Manual Assist stays
 * available either way.
 */
export function CustomerAiPause({
  scope,
  customerKey,
  canEdit,
}: {
  scope: CustomerAiPauseScope;
  customerKey: string;
  canEdit: boolean;
}) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const queryKey = ["customers", "ai-pause", scope, customerKey] as const;
  const [days, setDays] = useState<number | null>(DEFAULT_DAYS);
  const [reason, setReason] = useState("");

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

  return (
    <section
      className="space-y-2 rounded-lg border border-hairline bg-surface px-3.5 py-3 text-sm"
      aria-labelledby={`ai-pause-title-${scope}`}
      data-testid="customer-ai-pause"
      data-active={pause.active}
    >
      <h2 id={`ai-pause-title-${scope}`} className="text-sm font-semibold text-ink">
        {t("customerAiPause.title")}
      </h2>
      {pause.active ? (
        <div className="space-y-2">
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
          {canEdit && (
            <Button
              size="sm"
              variant="secondary"
              disabled={liftMutation.isPending}
              onClick={() => liftMutation.mutate()}
              data-testid="customer-ai-pause-lift"
            >
              {t("customerAiPause.lift")}
            </Button>
          )}
        </div>
      ) : (
        <div className="space-y-2">
          <p className="text-xs text-muted" data-testid="customer-ai-pause-state">
            {t("customerAiPause.off")}
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={days === null ? "forever" : String(days)}
              onChange={(e) => setDays(e.target.value === "forever" ? null : Number(e.target.value))}
              aria-label={t("customerAiPause.duration")}
              data-testid="customer-ai-pause-days"
              className="h-8 rounded-md border border-hairline bg-surface px-2 text-sm text-ink"
            >
              {DAY_CHOICES.map((d) => (
                <option key={d ?? "forever"} value={d === null ? "forever" : String(d)}>
                  {d === null
                    ? t("customerAiPause.untilLifted")
                    : t("customerAiPause.days", { count: d })}
                </option>
              ))}
            </select>
            <input
              type="text"
              value={reason}
              maxLength={255}
              onChange={(e) => setReason(e.target.value)}
              placeholder={t("customerAiPause.reasonPlaceholder")}
              aria-label={t("customerAiPause.reason")}
              data-testid="customer-ai-pause-reason"
              className="h-8 min-w-0 flex-1 rounded-md border border-hairline bg-surface px-2 text-sm text-ink"
            />
            <Button
              size="sm"
              variant="secondary"
              disabled={setMutation.isPending}
              onClick={() => setMutation.mutate()}
              data-testid="customer-ai-pause-set"
            >
              {t("customerAiPause.pause")}
            </Button>
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
