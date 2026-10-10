import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import type { WidgetProps } from "@/plugins/dashboardWidgets";
import { api, type QueueNode } from "@/lib/api";
import { flattenQueues } from "@/components/agent/QueueTree";
import { queueHasWork } from "../legacy";
import { linkTabIndex, rendersLinks, skeletonRows } from "../chrome/mode";
import { WidgetFrame } from "../chrome/WidgetFrame";
import { WidgetEmpty } from "../chrome/WidgetDataBody";
import { WidgetError } from "../chrome/WidgetError";
import { WidgetSkeleton } from "../chrome/WidgetSkeleton";

export interface QueueOverviewSettings {
  limit: number;
  only_with_work: boolean;
}

const open = (q: QueueNode) => q.counts?.open ?? 0;
const fresh = (q: QueueNode) => q.counts?.new ?? 0;

/** The shown queues: busiest first (open, then new, then name), optionally only those with work. */
function pickQueues(tree: QueueNode[], limit: number, onlyWithWork: boolean): QueueNode[] {
  return flattenQueues(tree)
    .filter((q) => !onlyWithWork || queueHasWork(q))
    .sort((a, b) => open(b) - open(a) || fresh(b) - fresh(a) || a.name.localeCompare(b.name))
    .slice(0, Math.max(1, limit));
}

/** Queues with open/new counts and a bar each (mockup); reads the cached `["queues"]` tree. */
export function QueueOverviewWidget({ settings, size, mode }: WidgetProps<QueueOverviewSettings>) {
  const { t } = useTranslation();
  const queuesQ = useQuery({ queryKey: ["queues"], queryFn: () => api.listQueues() });
  const shown = pickQueues(queuesQ.data ?? [], settings.limit, settings.only_with_work);
  const max = Math.max(1, ...shown.map((q) => open(q) + fresh(q)));
  const pct = (n: number) => `${Math.round((n / max) * 1000) / 10}%`;
  return (
    <WidgetFrame
      title={t("dashboards.widgets.queueOverview.title")}
      link={{ label: t("dashboards.widgets.queueOverview.all"), to: "/agent/queues" }}
      mode={mode}
      testId="queue-overview-widget"
    >
      {queuesQ.isPending ? (
        <WidgetSkeleton rows={skeletonRows(size.h)} />
      ) : queuesQ.isError ? (
        <WidgetError message={t("dashboards.state.loadFailed")} onRetry={() => void queuesQ.refetch()} />
      ) : shown.length === 0 ? (
        <WidgetEmpty>
          {settings.only_with_work ? t("dashboards.widgets.queueOverview.empty") : t("dashboards.widgets.queueOverview.emptyNone")}
        </WidgetEmpty>
      ) : (
        <ul className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,220px),1fr))] gap-x-6 gap-y-3">
          {shown.map((q) => {
            const name = q.name.split("::").pop() ?? q.name;
            return (
              <li key={q.id} data-testid={`queue-row-${q.id}`} className="flex min-w-0 flex-col gap-1.5">
                <div className="flex items-baseline justify-between gap-2 text-sm">
                  {rendersLinks(mode) ? (
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
                    {t("dashboards.widgets.queueOverview.counts", { open: open(q), new: fresh(q) })}
                  </span>
                </div>
                <div aria-hidden="true" className="flex h-1.5 overflow-hidden rounded-full bg-surface-subtle">
                  <span className="bg-accent" style={{ width: pct(open(q)) }} />
                  <span className="bg-green" style={{ width: pct(fresh(q)) }} />
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </WidgetFrame>
  );
}
