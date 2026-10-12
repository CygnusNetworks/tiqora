import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { SelectField } from "@/components/ui/SelectField";
import { reportTitle, type ReportItem } from "@/lib/reportsApi";
import type { ScheduleItem, ScheduleItemIn } from "@/lib/reportSchedulesApi";
import { MAX_ITEMS } from "./scheduleForm";
import { RANGE_PRESETS, presetOf, type PresetKey } from "./scheduleUi";

/**
 * The reports of one schedule: an ordered list (at most 10) taken from the catalogue,
 * with move up / down / remove and an optional "Zeitraum für den Versand" per report.
 */
export function ScheduleReportsPicker({
  value,
  onChange,
  catalogue,
  views = [],
  error,
}: {
  value: ScheduleItemIn[];
  onChange: (next: ScheduleItemIn[]) => void;
  catalogue: ReportItem[];
  /** The server's view of the stored items (availability). */
  views?: ScheduleItem[];
  error?: string;
}) {
  const { t } = useTranslation();
  const titleOf = (key: string) => {
    const r = catalogue.find((c) => c.key === key);
    return r ? reportTitle(r, t) : (views.find((v) => v.report_key === key)?.title ?? key);
  };
  const move = (i: number, d: -1 | 1) => {
    const j = i + d;
    if (j < 0 || j >= value.length) return;
    const next = [...value];
    [next[i], next[j]] = [next[j], next[i]];
    onChange(next);
  };
  const setRange = (i: number, key: PresetKey | "custom") => {
    if (key === "custom") return;
    const preset = RANGE_PRESETS[key];
    const overrides = { ...value[i].overrides };
    if (preset) overrides.time_range = { ...preset };
    else delete overrides.time_range;
    onChange(value.map((it, n) => (n === i ? { ...it, overrides } : it)));
  };
  const items = catalogue
    .filter((c) => c.definition != null)
    .map((c) => ({ value: c.key, label: reportTitle(c, t) }));

  return (
    <div className="space-y-3" data-testid="schedule-reports">
      {value.length > 0 && (
        <ol className="space-y-2">
          {value.map((it, i) => {
            // the server rows are in saved order: only trusted while the entry at this position is still the same report
            const view = views[i]?.report_key === it.report_key ? views[i] : undefined;
            const preset = presetOf(it.overrides?.time_range ?? null);
            const rangeItems = [
              ...(preset === "custom" ? [{ value: "custom" as const, label: t("reports.schedules.range.custom") }] : []),
              ...(Object.keys(RANGE_PRESETS) as PresetKey[]).map((k) => ({ value: k, label: t(`reports.schedules.range.${k}`) })),
            ];
            return (
              <li
                key={`${it.report_key}-${i}`}
                className="flex flex-wrap items-center gap-2 rounded-md border border-hairline bg-surface px-3 py-2"
                data-testid={`schedule-report-${i}`}
              >
                <span className="min-w-0 flex-1 text-sm font-medium text-ink">
                  {titleOf(it.report_key)}
                  {view && !view.available && (
                    <span className="mt-0.5 block text-xs font-normal text-danger" data-testid={`schedule-report-${i}-problem`}>
                      {view.problem ?? t("reports.schedules.unavailable")}
                    </span>
                  )}
                </span>
                <div className="w-52">
                  <SelectField
                    items={rangeItems}
                    value={preset}
                    onChange={(k) => setRange(i, k)}
                    aria-label={t("reports.schedules.range.label")}
                    testId={`schedule-report-${i}-range`}
                  />
                </div>
                <div className="flex gap-1">
                  <Button size="sm" variant="ghost" disabled={i === 0} onClick={() => move(i, -1)} aria-label={t("reports.schedules.moveUp")} data-testid={`schedule-report-${i}-up`}>
                    ↑
                  </Button>
                  <Button size="sm" variant="ghost" disabled={i === value.length - 1} onClick={() => move(i, 1)} aria-label={t("reports.schedules.moveDown")} data-testid={`schedule-report-${i}-down`}>
                    ↓
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => onChange(value.filter((_, n) => n !== i))} aria-label={t("reports.schedules.remove", { name: titleOf(it.report_key) })} data-testid={`schedule-report-${i}-remove`}>
                    ×
                  </Button>
                </div>
              </li>
            );
          })}
        </ol>
      )}
      {value.length < MAX_ITEMS && (
        <SelectField
          items={items}
          value={null}
          onChange={(key) => onChange([...value, { report_key: key, overrides: {} }])}
          placeholder={t("reports.schedules.addReport")}
          aria-label={t("reports.schedules.addReport")}
          testId="schedule-reports-add"
        />
      )}
      {error && (
        <p className="text-xs text-danger" role="alert" data-testid="schedule-reports-error">
          {t(error)}
        </p>
      )}
    </div>
  );
}
