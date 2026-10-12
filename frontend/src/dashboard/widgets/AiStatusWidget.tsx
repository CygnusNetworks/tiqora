import { useTranslation } from "react-i18next";
import type { WidgetProps } from "@/plugins/dashboardWidgets";
import { cn } from "@/lib/cn";
import { WidgetFrame } from "../chrome/WidgetFrame";
import { WidgetDataBody } from "../chrome/WidgetDataBody";

export type AiStatusSettings = Record<string, never>;

export interface AiBudget {
  kind: string;
  subject_name: string;
  window: string;
  used: number;
  limit: number;
  exhausted: boolean;
  resets_at: string;
  currency?: string | null;
}

export interface AiStatusData {
  ai_escalated: number;
  runs_today: Record<string, number>;
  /** Absent on a wallboard without details. */
  budgets?: AiBudget[];
}

const nf = (n: number) => new Intl.NumberFormat("de-DE", { maximumFractionDigits: 1 }).format(n);

/** Compact amount: 1.9 Mio. / 42 Tsd. for token budgets, currency for money budgets. */
function amount(n: number, currency?: string | null): string {
  if (currency) return new Intl.NumberFormat("de-DE", { style: "currency", currency, maximumFractionDigits: 2 }).format(n);
  if (n >= 1_000_000) return `${nf(n / 1_000_000)} Mio.`;
  if (n >= 1_000) return `${nf(n / 1_000)} Tsd.`;
  return nf(n);
}

/** AI handovers to humans, today's AI runs per feature, and the usage of the AI budgets. */
export function AiStatusWidget({ size, mode }: WidgetProps<AiStatusSettings>) {
  const { t } = useTranslation();
  return (
    <WidgetFrame title={t("dashboards.widgets.aiStatus.title")} mode={mode} testId="ai-status-widget">
      <WidgetDataBody<AiStatusData> h={size.h}>
        {(d) => {
          const runs = Object.entries(d?.runs_today ?? {}).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
          const total = runs.reduce((sum, [, n]) => sum + n, 0);
          const budgets = Array.isArray(d?.budgets) ? d.budgets : [];
          return (
            <div className="flex h-full min-h-0 flex-col gap-3">
              <div className="flex items-end gap-6">
                <div>
                  <div
                    className={cn("font-mono text-3xl font-semibold tabular-nums", (d?.ai_escalated ?? 0) > 0 ? "text-danger" : "text-ink")}
                    data-testid="ai-escalated"
                  >
                    {d?.ai_escalated ?? 0}
                  </div>
                  <div className="text-xs text-muted">{t("dashboards.widgets.aiStatus.handover")}</div>
                </div>
                <div>
                  <div className="font-mono text-xl font-semibold tabular-nums text-ink" data-testid="ai-runs">
                    {total}
                  </div>
                  <div className="text-xs text-muted" title={runs.map(([f, n]) => `${f}: ${n}`).join(", ")}>
                    {t("dashboards.widgets.aiStatus.runsToday")}
                  </div>
                </div>
              </div>
              {budgets.length > 0 && (
                <ul className="flex min-h-0 flex-col gap-2.5 overflow-hidden" aria-label={t("dashboards.widgets.aiStatus.budget")}>
                  {budgets.map((b, i) => {
                    const share = b.limit > 0 ? Math.min(1, b.used / b.limit) : b.exhausted ? 1 : 0;
                    const tone = b.exhausted ? "danger" : share >= 0.8 ? "warn" : "ok";
                    return (
                      <li key={`${b.kind}-${b.subject_name}-${i}`} data-testid={`ai-budget-${i}`} data-tone={tone} className="flex flex-col gap-1">
                        <div className="flex items-baseline justify-between gap-2 text-xs">
                          <span className="min-w-0 truncate text-ink">{b.subject_name}</span>
                          <span className={cn("shrink-0 font-mono tabular-nums", b.exhausted ? "text-danger" : "text-muted")}>
                            {b.exhausted
                              ? t("dashboards.widgets.aiStatus.exhausted")
                              : t("dashboards.widgets.aiStatus.available", { left: amount(Math.max(0, b.limit - b.used), b.currency) })}
                          </span>
                        </div>
                        <div
                          role="progressbar"
                          aria-valuemin={0}
                          aria-valuemax={100}
                          aria-valuenow={Math.round(share * 100)}
                          aria-label={b.subject_name}
                          className="h-1.5 overflow-hidden rounded-full bg-surface-subtle"
                        >
                          <span
                            className={cn("block h-full", tone === "danger" ? "bg-danger" : tone === "warn" ? "bg-escalation" : "bg-accent")}
                            style={{ width: `${Math.round(share * 1000) / 10}%` }}
                          />
                        </div>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          );
        }}
      </WidgetDataBody>
    </WidgetFrame>
  );
}
