import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import type { WidgetProps } from "@/plugins/dashboardWidgets";
import { linkTabIndex, rendersLinks } from "../chrome/mode";
import { useIsKiosk } from "../wallboard/kioskContext";
import { WidgetFrame } from "../chrome/WidgetFrame";
import { WidgetDataBody, WidgetEmpty } from "../chrome/WidgetDataBody";

export interface QueueOverviewSettings {
  limit: number;
  only_with_work: boolean;
}

export interface QueueOverviewRow {
  id: number;
  name: string;
  open: number;
  new: number;
  escalated: number;
}

export interface QueueOverviewData {
  queues: QueueOverviewRow[];
  total: number;
}

/**
 * Queues with open/new/escalated counts and a stacked bar each. The server picks, orders and caps
 * the queues (busiest first), so the same data serves the wallboard, which has no queue tree.
 * Escalated tickets are open ones: the bar shows open-without-escalated, new and escalated.
 */
export function QueueOverviewWidget({ settings, size, mode }: WidgetProps<QueueOverviewSettings>) {
  const { t } = useTranslation();
  const kiosk = useIsKiosk();
  const board = mode === "wallboard";
  return (
    <WidgetFrame
      scaleBody={false}
      title={t("dashboards.widgets.queueOverview.title")}
      link={{ label: t("dashboards.widgets.queueOverview.all"), to: "/agent/queues" }}
      mode={mode}
      testId="queue-overview-widget"
    >
      <WidgetDataBody<QueueOverviewData> h={size.h}>
        {(d) => {
          const shown = Array.isArray(d?.queues) ? d.queues : [];
          if (shown.length === 0)
            return (
              <WidgetEmpty>
                {settings.only_with_work
                  ? t("dashboards.widgets.queueOverview.empty")
                  : t("dashboards.widgets.queueOverview.emptyNone")}
              </WidgetEmpty>
            );
          const max = Math.max(1, ...shown.map((q) => q.open + q.new));
          const pct = (n: number) => `${Math.round((Math.max(0, n) / max) * 1000) / 10}%`;
          if (board)
            return (
              <div className="flex h-full min-h-0 flex-col justify-between gap-[0.6em]" data-testid="queue-overview-wallboard">
                <ul className="flex min-h-0 flex-col gap-[0.6em]">
                  {shown.map((q) => {
                    const name = q.name.split("::").pop() ?? q.name;
                    const escalated = Math.min(q.escalated, q.open);
                    return (
                      <li
                        key={q.id}
                        data-testid={`queue-row-${q.id}`}
                        className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.6fr)_auto] items-center gap-[0.8em] text-[length:calc(22px*var(--wb-zoom,1))]"
                      >
                        <span className="truncate text-ink" title={q.name}>
                          {name}
                        </span>
                        <div
                          aria-hidden="true"
                          className="flex h-[1em] overflow-hidden rounded-full bg-surface-subtle"
                          data-testid={`queue-bar-${q.id}`}
                        >
                          <span data-segment="open" className="bg-accent" style={{ width: pct(q.open - escalated) }} />
                          <span data-segment="new" className="bg-green" style={{ width: pct(q.new) }} />
                          <span data-segment="escalated" className="bg-danger" style={{ width: pct(escalated) }} />
                        </div>
                        <span className="font-mono tabular-nums text-ink">
                          {t("dashboards.widgets.queueOverview.wallboardCounts", { open: q.open, new: q.new })}
                        </span>
                      </li>
                    );
                  })}
                </ul>
                <p
                  className="flex items-center gap-[1em] text-[length:calc(16px*var(--wb-zoom,1))] text-muted"
                  data-testid="queue-overview-legend"
                >
                  <LegendDot className="bg-accent" label={t("dashboards.widgets.queueOverview.legend.open")} />
                  <LegendDot className="bg-green" label={t("dashboards.widgets.queueOverview.legend.new")} />
                  <LegendDot className="bg-danger" label={t("dashboards.widgets.queueOverview.legend.escalated")} />
                </p>
              </div>
            );
          return (
            <div className="flex flex-col gap-3">
              <ul className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,220px),1fr))] gap-x-6 gap-y-3">
                {shown.map((q) => {
                  const name = q.name.split("::").pop() ?? q.name;
                  const escalated = Math.min(q.escalated, q.open);
                  return (
                    <li key={q.id} data-testid={`queue-row-${q.id}`} className="flex min-w-0 flex-col gap-1.5">
                      <div className="flex items-baseline justify-between gap-2 text-sm">
                        {rendersLinks(mode, kiosk) ? (
                          <Link
                            to="/agent/queues"
                            search={{ queue_id: q.id }}
                            tabIndex={linkTabIndex(mode)}
                            title={q.name}
                            className="min-w-0 truncate text-ink hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
                          >
                            {name}
                          </Link>
                        ) : (
                          <span className="min-w-0 truncate text-ink" title={q.name}>
                            {name}
                          </span>
                        )}
                        <span className="shrink-0 font-mono text-xs tabular-nums text-muted">
                          {t("dashboards.widgets.queueOverview.counts", { open: q.open, new: q.new })}
                          {q.escalated > 0 && (
                            <span className="text-danger" data-testid={`queue-escalated-${q.id}`}>
                              {t("dashboards.widgets.queueOverview.escalatedCount", { count: q.escalated })}
                            </span>
                          )}
                        </span>
                      </div>
                      <div aria-hidden="true" className="flex h-1.5 overflow-hidden rounded-full bg-surface-subtle">
                        <span data-segment="open" className="bg-accent" style={{ width: pct(q.open - escalated) }} />
                        <span data-segment="new" className="bg-green" style={{ width: pct(q.new) }} />
                        <span data-segment="escalated" className="bg-danger" style={{ width: pct(escalated) }} />
                      </div>
                    </li>
                  );
                })}
              </ul>
              <p className="flex items-center gap-3 text-xs text-muted" data-testid="queue-overview-legend">
                <LegendDot className="bg-accent" label={t("dashboards.widgets.queueOverview.legend.open")} />
                <LegendDot className="bg-green" label={t("dashboards.widgets.queueOverview.legend.new")} />
                <LegendDot className="bg-danger" label={t("dashboards.widgets.queueOverview.legend.escalated")} />
              </p>
            </div>
          );
        }}
      </WidgetDataBody>
    </WidgetFrame>
  );
}

function LegendDot({ className, label }: { className: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span aria-hidden="true" className={`h-2 w-2 rounded-full ${className}`} />
      {label}
    </span>
  );
}
