import { useId, type Dispatch } from "react";
import { useTranslation } from "react-i18next";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { SelectField } from "@/components/ui/SelectField";
import { Switch } from "@/components/ui/Switch";
import type { ReportSource } from "@/lib/reportsApi";
import {
  allowedVisualizations,
  type BuilderAction,
  type BuilderState,
  type VisualizationType,
} from "./builderState";
import { ordered, withSeries } from "./builderSeries";
import { Section, inputCls } from "./builderUi";

const SERIES_VIZ: ReadonlySet<VisualizationType> = new Set(["bar", "stacked_bar", "line", "area", "pivot", "heatmap"]);
const PLOTTED_VIZ: ReadonlySet<VisualizationType> = new Set(["bar", "stacked_bar", "line", "area", "donut", "heatmap"]);

/** Right column of the builder: how the result is shown, totals, sort and row limit. */
export function BuilderOptionsPanel({
  state,
  dispatch,
  source,
}: {
  state: BuilderState;
  dispatch: Dispatch<BuilderAction>;
  source: ReportSource | undefined;
}) {
  const { t } = useTranslation();
  const id = useId();
  const type = state.visualization.type ?? "table";
  const allowed = allowedVisualizations(state, source);
  const vizItems = (allowed.includes(type) ? allowed : [type, ...allowed]).map((v) => ({
    value: v,
    label: t(`reports.builder.viz.${v}`),
  }));
  const dims = ordered(state);
  const dimLabel = (key: string) => {
    const d = source?.dimensions.find((x) => x.key === key);
    return d ? t(d.label_key) : key;
  };
  const measureLabel = (key: string) => {
    const m = source?.measures.find((x) => x.key === key);
    return m ? t(m.label_key) : key;
  };
  const showSeries = dims.length >= 2 && SERIES_VIZ.has(type);
  const showPlotted = state.measures.length >= 2 && PLOTTED_VIZ.has(type);
  const plotted = state.visualization.chart_measures ?? state.measures.map((_, i) => i);

  const togglePlotted = (i: number) => {
    const next = plotted.includes(i) ? plotted.filter((x) => x !== i) : [...plotted, i].sort((a, b) => a - b);
    if (next.length === 0) return; // at least one value stays shown
    dispatch({ type: "setVisualization", patch: { chart_measures: next.length === state.measures.length ? null : next } });
  };

  const sort = state.sort[0];
  const sortItems = [
    { value: "", label: t("reports.builder.sortNone") },
    ...dims.map((d) => ({ value: d.key, label: dimLabel(d.key) })),
    ...state.measures.map((m) => ({ value: m, label: measureLabel(m) })),
  ];
  const setSort = (key: string, direction: "asc" | "desc") =>
    dispatch({ type: "setSort", sort: key === "" ? [] : [{ key, direction }, ...state.sort.slice(1)] });

  return (
    <div className="space-y-5" data-testid="builder-options">
      <Section title={t("reports.builder.visualization")}>
        <div className="space-y-1">
          <span className="block text-xs text-muted">{t("reports.builder.chartType")}</span>
          <SelectField<VisualizationType>
            items={vizItems}
            value={type}
            onChange={(v) => dispatch({ type: "setVisualization", patch: { type: v } })}
            testId="builder-viz"
            aria-label={t("reports.builder.chartType")}
          />
        </div>
        {showSeries && (
          <div className="space-y-1">
            <span className="block text-xs text-muted">{t("reports.builder.series")}</span>
            <SelectField<string>
              items={[{ value: "", label: t("reports.builder.seriesNone") }, ...dims.map((d) => ({ value: d.key, label: dimLabel(d.key) }))]}
              value={state.columns[0]?.key ?? ""}
              onChange={(key) => dispatch({ type: "load", state: withSeries(state, key === "" ? null : key) })}
              testId="builder-series"
              aria-label={t("reports.builder.series")}
            />
          </div>
        )}
        {showPlotted && (
          <fieldset className="space-y-1">
            <legend className="text-xs text-muted">{t("reports.builder.plotted")}</legend>
            {state.measures.map((m, i) => (
              <label key={m} className="flex items-center gap-2 text-sm text-ink">
                <input
                  type="checkbox"
                  checked={plotted.includes(i)}
                  onChange={() => togglePlotted(i)}
                  className="h-4 w-4 rounded border-hairline"
                  data-testid={`builder-plotted-${i}`}
                />
                {measureLabel(m)}
              </label>
            ))}
          </fieldset>
        )}
      </Section>

      <Section title={t("reports.builder.totals")}>
        {(
          [
            ["grand", "totalsGrand"],
            ["rows", "totalsRows"],
            ["columns", "totalsColumns"],
          ] as const
        ).map(([key, label]) => (
          <div key={key} className="flex items-center gap-2 text-sm text-ink">
            <Switch
              checked={state.totals[key] ?? false}
              onChange={(on) => dispatch({ type: "setTotals", patch: { [key]: on } })}
              testId={`builder-totals-${key}`}
              aria-labelledby={`${id}-${key}`}
            />
            <span id={`${id}-${key}`}>{t(`reports.builder.${label}`)}</span>
          </div>
        ))}
      </Section>

      <Section title={t("reports.builder.sort")}>
        <SelectField<string>
          items={sortItems}
          value={sort?.key ?? ""}
          onChange={(key) => setSort(key, sort?.direction ?? "desc")}
          testId="builder-sort-key"
          aria-label={t("reports.builder.sort")}
        />
        {sort && (
          <SegmentedControl
            items={[
              { value: "asc", label: t("reports.builder.sortAsc") },
              { value: "desc", label: t("reports.builder.sortDesc") },
            ]}
            value={sort.direction ?? "desc"}
            onChange={(direction) => setSort(sort.key, direction)}
            testId="builder-sort-dir"
            aria-label={t("reports.builder.sort")}
          />
        )}
      </Section>

      <Section title={t("reports.builder.limit")} hint={t("reports.builder.limitHint")}>
        <input
          type="number"
          min={1}
          max={10000}
          inputMode="numeric"
          className={`${inputCls} w-full`}
          value={state.limit ?? ""}
          onChange={(e) => dispatch({ type: "setLimit", limit: e.target.value === "" ? null : Number(e.target.value) })}
          aria-label={t("reports.builder.limit")}
          data-testid="builder-limit"
        />
      </Section>
    </div>
  );
}
