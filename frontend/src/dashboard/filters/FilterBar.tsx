import { useId, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { flattenQueues } from "@/components/agent/QueueTree";
import { ChevronDownIcon } from "@/components/ui/icons";
import { Popover } from "@/components/ui/Popover";
import { usePopoverClose } from "@/components/ui/popoverContext";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import {
  DashboardConflictError,
  dashboardKeys,
  dashboardsApi,
  type Dashboard,
} from "@/lib/dashboardsApi";
import { filtersActive } from "./filtersContext";
import { TIME_RANGE_PRESETS, timeRangeLabelKey, type TimeRangePreset } from "./timeRanges";

const CHIP =
  "inline-flex min-h-8 max-w-full items-center gap-1.5 rounded-full border px-3 py-1 text-[13px] transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";
const BUTTON =
  "rounded-lg px-3 py-1.5 text-[13px] transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";

type Draft = { queue_ids: number[]; time_range: TimeRangePreset | null };

/** Queue names for the chip; ids no queue list entry names (yet) fall back to a count. */
function useQueueNames(enabled: boolean) {
  const q = useQuery({ queryKey: ["queues"], queryFn: () => api.listQueues(), enabled, staleTime: 60_000 });
  const flat = flattenQueues(q.data ?? []);
  return { flat, loading: q.isLoading, failed: q.isError, nameOf: new Map(flat.map((n) => [n.id, n.name])) };
}

function PopoverBody({
  title,
  onApply,
  onReset,
  children,
}: {
  title: string;
  onApply: () => void;
  onReset: () => void;
  children: React.ReactNode;
}) {
  const { t } = useTranslation();
  const close = usePopoverClose();
  return (
    <div className="space-y-3">
      <p className="text-xs font-medium text-muted">{title}</p>
      {children}
      <div className="flex justify-end gap-2 border-t border-hairline pt-2">
        <button
          type="button"
          onClick={() => {
            onReset();
            close();
          }}
          className={cn(BUTTON, "border border-hairline bg-surface text-ink hover:bg-surface-subtle")}
        >
          {t("dashboards.filters.reset")}
        </button>
        <button
          type="button"
          onClick={() => {
            onApply();
            close();
          }}
          className={cn(BUTTON, "bg-accent font-semibold text-accent-ink hover:bg-accent/90")}
        >
          {t("dashboards.filters.apply")}
        </button>
      </div>
    </div>
  );
}

function QueuePicker({ current, onSave }: { current: number[]; onSave: (ids: number[]) => void }) {
  const { t } = useTranslation();
  const uid = useId();
  const [ids, setIds] = useState<number[]>(current);
  const { flat, loading, failed } = useQueueNames(true);
  const toggle = (id: number) => setIds((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]));
  return (
    <PopoverBody title={t("dashboards.filters.queues")} onApply={() => onSave(ids)} onReset={() => onSave([])}>
      {loading && <p className="text-sm text-muted">{t("dashboards.filters.queuesLoading")}</p>}
      {failed && <p className="text-sm text-danger">{t("dashboards.filters.queuesFailed")}</p>}
      {!loading && !failed && (
        <ul className="max-h-56 space-y-0.5 overflow-y-auto">
          <li>
            <label className="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm text-ink hover:bg-surface-subtle">
              <input type="checkbox" checked={ids.length === 0} onChange={() => setIds([])} />
              {t("dashboards.filters.allQueues")}
            </label>
          </li>
          {flat.map((q) => (
            <li key={`${uid}-${q.id}`}>
              <label className="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm text-ink hover:bg-surface-subtle">
                <input type="checkbox" checked={ids.includes(q.id)} onChange={() => toggle(q.id)} />
                <span className="truncate">{q.name}</span>
              </label>
            </li>
          ))}
        </ul>
      )}
    </PopoverBody>
  );
}

function RangePicker({ current, onSave }: { current: TimeRangePreset | null; onSave: (r: TimeRangePreset | null) => void }) {
  const { t } = useTranslation();
  const name = useId();
  const [range, setRange] = useState<TimeRangePreset | null>(current);
  return (
    <PopoverBody title={t("dashboards.filters.timeRange")} onApply={() => onSave(range)} onReset={() => onSave(null)}>
      <div role="radiogroup" aria-label={t("dashboards.filters.timeRange")} className="space-y-0.5">
        <label className="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm text-ink hover:bg-surface-subtle">
          <input type="radio" name={name} checked={range === null} onChange={() => setRange(null)} />
          {t("dashboards.filters.asReport")}
        </label>
        {TIME_RANGE_PRESETS.map((preset) => (
          <label key={preset} className="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm text-ink hover:bg-surface-subtle">
            <input type="radio" name={name} checked={range === preset} onChange={() => setRange(preset)} />
            {t(timeRangeLabelKey(preset))}
          </label>
        ))}
      </div>
    </PopoverBody>
  );
}

/**
 * The dashboard-wide filter bar under the tab row: queue chip and time-range chip. Editors change
 * the filters in view mode (saved at once, `PATCH /filters`); read-only viewers see the active
 * ones as plain chips; in edit mode the chips are disabled (a PATCH would invalidate the open
 * edit session's version).
 */
export function FilterBar({
  dashboard,
  disabled = false,
}: {
  dashboard: Pick<Dashboard, "id" | "version" | "can_edit" | "filters">;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const filters: Draft = { queue_ids: dashboard.filters?.queue_ids ?? [], time_range: dashboard.filters?.time_range ?? null };
  const active = filtersActive(filters);
  const [message, setMessage] = useState<string | null>(null);
  const { nameOf } = useQueueNames(filters.queue_ids.length > 0);

  const save = useMutation({
    mutationFn: (next: Draft) => dashboardsApi.patchFilters(dashboard.id, { filters: next, version: dashboard.version }),
    onSuccess: (saved) => {
      setMessage(null);
      qc.setQueryData(dashboardKeys.one(dashboard.id), saved);
      void qc.invalidateQueries({ queryKey: dashboardKeys.data(dashboard.id) });
      void qc.invalidateQueries({ queryKey: dashboardKeys.list() });
    },
    onError: (err) => {
      setMessage(err instanceof DashboardConflictError ? t("dashboards.filters.conflict") : t("dashboards.filters.saveFailed"));
      if (err instanceof DashboardConflictError) {
        void qc.invalidateQueries({ queryKey: dashboardKeys.one(dashboard.id) });
        void qc.invalidateQueries({ queryKey: dashboardKeys.data(dashboard.id) });
        void qc.invalidateQueries({ queryKey: dashboardKeys.list() });
      }
    },
  });

  if (!dashboard.can_edit && !active) return null;

  const queueValue =
    filters.queue_ids.length === 0
      ? t("dashboards.filters.allShort")
      : filters.queue_ids.every((id) => nameOf.has(id))
        ? filters.queue_ids.map((id) => nameOf.get(id)).join(", ")
        : t("dashboards.filters.queueCount", { count: filters.queue_ids.length });
  const rangeValue = filters.time_range ? t(timeRangeLabelKey(filters.time_range)) : t("dashboards.filters.asReport");
  const queueLabel = t("dashboards.filters.chip", { label: t("dashboards.filters.queues"), value: queueValue });
  const rangeLabel = t("dashboards.filters.chip", { label: t("dashboards.filters.timeRange"), value: rangeValue });
  const idle = "border-hairline bg-surface text-ink";
  const on = "border-accent/40 bg-accent/10 text-ink";
  const editable = dashboard.can_edit && !disabled;

  return (
    <div className="flex flex-wrap items-center gap-2" role="group" aria-label={t("dashboards.filters.label")} data-testid="filter-bar">
      {dashboard.can_edit ? (
        <>
          <Popover
            align="left"
            label={t("dashboards.filters.queues")}
            trigger={({ ref, toggleProps, open }) => (
              <button
                ref={ref}
                type="button"
                {...toggleProps}
                disabled={!editable || save.isPending}
                className={cn(CHIP, filters.queue_ids.length > 0 ? on : idle, "disabled:cursor-not-allowed disabled:opacity-60")}
              >
                <span className="truncate">{queueLabel}</span>
                <ChevronDownIcon className={cn("h-3.5 w-3.5 shrink-0 text-muted", open && "rotate-180")} />
              </button>
            )}
          >
            <QueuePicker current={filters.queue_ids} onSave={(ids) => save.mutate({ ...filters, queue_ids: ids })} />
          </Popover>
          <Popover
            align="left"
            label={t("dashboards.filters.timeRange")}
            trigger={({ ref, toggleProps, open }) => (
              <button
                ref={ref}
                type="button"
                {...toggleProps}
                disabled={!editable || save.isPending}
                className={cn(CHIP, filters.time_range ? on : idle, "disabled:cursor-not-allowed disabled:opacity-60")}
              >
                <span className="truncate">{rangeLabel}</span>
                <ChevronDownIcon className={cn("h-3.5 w-3.5 shrink-0 text-muted", open && "rotate-180")} />
              </button>
            )}
          >
            <RangePicker current={filters.time_range} onSave={(range) => save.mutate({ ...filters, time_range: range })} />
          </Popover>
        </>
      ) : (
        <span title={t("dashboards.filters.readOnly")} className="flex flex-wrap gap-2">
          {filters.queue_ids.length > 0 && <span className={cn(CHIP, on)}>{queueLabel}</span>}
          {filters.time_range && <span className={cn(CHIP, on)}>{rangeLabel}</span>}
        </span>
      )}
      {disabled && dashboard.can_edit && <span className="text-xs text-muted">{t("dashboards.filters.editHint")}</span>}
      {message && (
        <p role="alert" className="text-xs text-danger">
          {message}
        </p>
      )}
    </div>
  );
}
