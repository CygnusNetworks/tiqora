import { useId } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { flattenQueues } from "@/components/agent/QueueTree";
import { MultiSelectInput } from "@/components/fields/MultiSelectInput";
import { SelectMenu, type SelectMenuItem } from "@/components/ui/SelectMenu";
import { Tabs } from "@/components/ui/Tabs";
import { ChevronDownIcon } from "@/components/ui/icons";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import {
  DATE_RANGE_PRESETS,
  dateRangeForPreset,
  type DateRangePreset,
} from "@/lib/dateRanges";
import { GRAINS, type Grain, type ReportDefinition } from "@/lib/reportsApi";
import {
  choiceIndex,
  dateDimensionGrain,
  filterRange,
  hasQueueParameter,
  hasTimeRange,
  rangeInvalid,
  type ReportFilterState,
} from "./reportFilter";

const chipCls =
  "inline-flex items-center rounded border border-hairline px-2 py-0.5 text-xs text-muted transition-colors duration-100 hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";
const chipActiveCls = "border-accent/50 bg-accent-dim text-accent hover:text-accent";
const inputCls =
  "rounded-md border border-hairline bg-bg px-2 py-1 text-sm text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";

/**
 * Filters above a report: date presets (`stats-preset-<key>`), own dates, time
 * grouping (only with a date dimension), "split by" tabs (only with choices)
 * and the queue selection (only when the report takes queues).
 */
export function ReportFilterBar({
  definition,
  value,
  onChange,
  defaultRange,
  testId = "report-filter",
}: {
  definition: ReportDefinition;
  value: ReportFilterState;
  onChange: (v: ReportFilterState) => void;
  /** Dates the report's own default range resolved to (shown while no range is chosen). */
  defaultRange?: { from: string; to: string } | null;
  testId?: string;
}) {
  const { t } = useTranslation();
  const id = useId();
  const withRange = hasTimeRange(definition);
  const withQueues = hasQueueParameter(definition);
  const grain = dateDimensionGrain(definition, value.choice);
  const at = choiceIndex(definition);
  const dim = at >= 0 ? definition.dimensions?.[at] : undefined;

  const queuesQ = useQuery({ queryKey: ["queues"], queryFn: () => api.listQueues(), enabled: withQueues });
  const queueOptions = flattenQueues(queuesQ.data ?? []).map((q) => ({ value: String(q.id), label: q.name }));

  const grainItems: SelectMenuItem<Grain>[] = GRAINS.map((g) => ({ value: g, label: t(`reports.grains.${g}`) }));
  const shownGrain = value.grain ?? grain;

  const pickPreset = (preset: DateRangePreset) => {
    if (preset === "reset") {
      onChange({ ...value, preset: null, from: "", to: "" });
      return;
    }
    const range = dateRangeForPreset(preset);
    onChange({ ...value, preset, from: range.from, to: range.to });
  };
  const shown = value.preset === "custom" ? { from: value.from, to: value.to } : (filterRange(value) ?? defaultRange ?? { from: "", to: "" });
  const setDate = (field: "from" | "to", v: string) =>
    onChange({ ...value, ...shown, preset: "custom", [field]: v });

  return (
    <div className="space-y-3 rounded-lg border border-hairline bg-surface p-3" data-testid={testId}>
      {withRange && (
        <div className="space-y-2">
          <div
            className="flex flex-wrap items-center gap-1.5"
            role="group"
            aria-label={t("reports.timeRange")}
            data-testid={`${testId}-presets`}
          >
            {DATE_RANGE_PRESETS.map((key) => {
              const active = key === "reset" ? false : value.preset === key;
              return (
                <button
                  key={key}
                  type="button"
                  className={cn(chipCls, active && chipActiveCls)}
                  aria-pressed={key === "reset" ? undefined : active}
                  onClick={() => pickPreset(key)}
                  data-testid={`stats-preset-${key}`}
                >
                  {t(`reports.presets.${key}`)}
                </button>
              );
            })}
          </div>
        </div>
      )}
      <div className="flex flex-wrap items-end gap-3">
        {withRange && (
          <>
            <label className="flex flex-col gap-1 text-xs text-muted">
              {t("reports.from")}
              <input
                type="date"
                className={inputCls}
                value={shown.from}
                max={shown.to || undefined}
                aria-invalid={rangeInvalid(value) || undefined}
                aria-describedby={rangeInvalid(value) ? `${id}-range-error` : undefined}
                onChange={(e) => setDate("from", e.target.value)}
                data-testid={`${testId}-from`}
              />
            </label>
            <label className="flex flex-col gap-1 text-xs text-muted">
              {t("reports.to")}
              <input
                type="date"
                className={inputCls}
                value={shown.to}
                min={shown.from || undefined}
                aria-invalid={rangeInvalid(value) || undefined}
                aria-describedby={rangeInvalid(value) ? `${id}-range-error` : undefined}
                onChange={(e) => setDate("to", e.target.value)}
                data-testid={`${testId}-to`}
              />
            </label>
          </>
        )}
        {grain && shownGrain && (
          <div className="flex flex-col gap-1 text-xs text-muted">
            <span id={`${id}-grain`}>{t("reports.grain")}</span>
            <SelectMenu
              items={grainItems}
              value={shownGrain}
              onSelect={(g) => onChange({ ...value, grain: g === grain && value.grain === null ? null : g })}
              panelTestId={`${testId}-grain-panel`}
              trigger={({ open, ref, toggleProps }) => (
                <button
                  ref={ref}
                  type="button"
                  aria-labelledby={`${id}-grain ${id}-grain-value`}
                  data-testid={`${testId}-grain`}
                  {...toggleProps}
                  className="flex min-w-[7rem] items-center justify-between gap-2 rounded-md border border-hairline bg-bg px-2 py-1 text-sm text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
                >
                  <span id={`${id}-grain-value`}>{t(`reports.grains.${shownGrain}`)}</span>
                  <ChevronDownIcon
                    className={cn("shrink-0 text-muted transition-transform duration-150", open && "rotate-180")}
                  />
                </button>
              )}
            />
          </div>
        )}
        {withQueues && (
          <div className="flex min-w-[14rem] max-w-md flex-1 flex-col gap-1 text-xs text-muted">
            <span id={`${id}-queues`}>{t("reports.parameters.queues")}</span>
            <MultiSelectInput
              value={value.queues.map(String)}
              onChange={(next) => onChange({ ...value, queues: next.map(Number).filter(Number.isFinite) })}
              options={queueOptions}
              labelId={`${id}-queues`}
              testId={`${testId}-queues`}
            />
          </div>
        )}
      </div>
      {withRange && rangeInvalid(value) && (
        <p id={`${id}-range-error`} role="alert" className="text-xs text-danger" data-testid={`${testId}-range-error`}>
          {t("reports.rangeInvalid")}
        </p>
      )}
      {dim && (
        <div className="flex flex-wrap items-center gap-2" data-testid={`${testId}-choice`}>
          <span className="text-xs text-muted">{t("reports.groupBy")}</span>
          <Tabs
            value={value.choice ?? dim.key}
            onChange={(next) => onChange({ ...value, choice: next === dim.key ? null : next })}
            items={[dim.key, ...(dim.choices ?? [])].map((k) => ({
              id: k,
              label: t(`reports.dimensions.${k}`, { defaultValue: k }),
            }))}
          />
        </div>
      )}
    </div>
  );
}
