import { useId, useMemo, type Dispatch } from "react";
import { useTranslation } from "react-i18next";
import { fieldsFromSource } from "@/components/filters/filterFields";
import { FilterBuilder } from "@/components/filters/FilterBuilder";
import { MultiSelectInput } from "@/components/fields/MultiSelectInput";
import { SelectField } from "@/components/ui/SelectField";
import { cn } from "@/lib/cn";
import { dateRangeForPreset } from "@/lib/dateRanges";
import { GRAINS, type Grain, type ReportSource } from "@/lib/reportsApi";
import { listTimeZones } from "@/lib/timeZone";
import { BuilderParametersEditor } from "./BuilderParametersEditor";
import {
  MAX_DIMENSIONS,
  MAX_MEASURES,
  type BuilderAction,
  type BuilderIssue,
  type BuilderState,
  type DimensionSlot,
} from "./builderState";
import { AddMenu, IconButton, Section, inputCls } from "./builderUi";
import { BUILDER_PRESETS, presetOf, presetRange } from "./timePresets";

type SourceDimension = ReportSource["dimensions"][number];
type Target = "rows" | "columns";

const chipCls =
  "inline-flex items-center rounded border border-hairline px-2 py-0.5 text-xs text-muted transition-colors duration-100 hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";
const chipActiveCls = "border-accent/50 bg-accent-dim text-accent hover:text-accent";

const isDate = (d: SourceDimension | undefined) => d?.kind === "date" || d?.kind === "local_date";

function DimensionItem({
  target,
  index,
  slot,
  source,
  state,
  dispatch,
}: {
  target: Target;
  index: number;
  slot: DimensionSlot;
  source: ReportSource | undefined;
  state: BuilderState;
  dispatch: Dispatch<BuilderAction>;
}) {
  const { t } = useTranslation();
  const id = useId();
  const dim = source?.dimensions.find((d) => d.key === slot.key);
  const label = dim ? t(dim.label_key) : slot.key;
  const testId = `builder-${target}`;
  const update = (patch: Partial<DimensionSlot>) => dispatch({ type: "updateDimension", target, index, patch });
  const choiceOptions = (source?.dimensions ?? [])
    .filter((d) => d.key !== slot.key)
    .map((d) => ({ value: d.key, label: t(d.label_key) }));
  const columnAt = state.columnAt ?? state.rows.length;
  const first = target === "rows" ? index === 0 : columnAt <= 0;
  const last = target === "rows" ? index === state[target].length - 1 : columnAt >= state.rows.length;
  const date = slot.date || isDate(dim);

  return (
    <li className="space-y-2 rounded-md border border-hairline p-2" data-testid={`${testId}-item-${index}`}>
      <div className="flex items-center gap-1">
        <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink">{label}</span>
        <IconButton label={`${label}: ${t("reports.builder.moveUp")}`} onClick={() => dispatch({ type: "moveDimension", target, index, delta: -1 })} disabled={first} testId={`${testId}-up-${index}`}>
          ↑
        </IconButton>
        <IconButton label={`${label}: ${t("reports.builder.moveDown")}`} onClick={() => dispatch({ type: "moveDimension", target, index, delta: 1 })} disabled={last} testId={`${testId}-down-${index}`}>
          ↓
        </IconButton>
        <IconButton label={`${label}: ${t("reports.builder.remove")}`} onClick={() => dispatch({ type: "removeDimension", target, index })} testId={`${testId}-remove-${index}`}>
          ✕
        </IconButton>
      </div>
      <div className="grid grid-cols-2 gap-2">
        {date && (
          <div className="space-y-1">
            <span className="block text-xs text-muted">{t("reports.builder.grain")}</span>
            <SelectField<Grain>
              items={GRAINS.map((g) => ({ value: g, label: t(`reports.grains.${g}`) }))}
              value={slot.grain ?? "day"}
              onChange={(grain) => update({ grain })}
              testId={`${testId}-grain-${index}`}
              aria-label={`${label}: ${t("reports.builder.grain")}`}
            />
          </div>
        )}
        <div className="space-y-1">
          <label htmlFor={`${id}-topn`} className="block text-xs text-muted">
            {t("reports.builder.topN")}
          </label>
          <input
            id={`${id}-topn`}
            type="number"
            min={1}
            max={100}
            inputMode="numeric"
            className={cn(inputCls, "w-full")}
            placeholder={t("reports.builder.topNPlaceholder")}
            value={slot.topN ?? ""}
            onChange={(e) => update({ topN: e.target.value === "" ? null : Number(e.target.value) })}
            data-testid={`${testId}-topn-${index}`}
          />
        </div>
      </div>
      {choiceOptions.length > 0 && (
        <div className="space-y-1">
          <span id={`${id}-choices`} className="block text-xs text-muted">
            {t("reports.builder.choices")}
          </span>
          <MultiSelectInput
            value={slot.choices}
            onChange={(choices) => update({ choices })}
            options={choiceOptions}
            labelId={`${id}-choices`}
            testId={`${testId}-choices-${index}`}
          />
        </div>
      )}
    </li>
  );
}

function DimensionList({
  target,
  title,
  state,
  source,
  dispatch,
}: {
  target: Target;
  title: string;
  state: BuilderState;
  source: ReportSource | undefined;
  dispatch: Dispatch<BuilderAction>;
}) {
  const { t } = useTranslation();
  const used = new Set([...state.rows, ...state.columns].map((d) => d.key));
  const available = (source?.dimensions ?? []).filter((d) => !used.has(d.key));
  const full = state.rows.length + state.columns.length >= MAX_DIMENSIONS || (target === "columns" && state.columns.length >= 1);
  const slots = state[target];
  return (
    <Section title={title} testId={`builder-${target}`}>
      {slots.length > 0 && (
        <ul className="space-y-2">
          {slots.map((slot, i) => (
            <DimensionItem key={slot.key} target={target} index={i} slot={slot} source={source} state={state} dispatch={dispatch} />
          ))}
        </ul>
      )}
      <AddMenu
        label={t("reports.builder.addDimension")}
        items={available.map((d) => ({ value: d.key, label: t(d.label_key) }))}
        onPick={(key) => dispatch({ type: "addDimension", target, key, date: isDate(source?.dimensions.find((d) => d.key === key)) })}
        disabled={!source || full}
        testId={`builder-${target}-add`}
      />
    </Section>
  );
}

function ValueList({ state, source, dispatch }: { state: BuilderState; source: ReportSource | undefined; dispatch: Dispatch<BuilderAction> }) {
  const { t } = useTranslation();
  const labelOf = (key: string) => {
    const m = source?.measures.find((x) => x.key === key);
    return m ? t(m.label_key) : key;
  };
  const available = (source?.measures ?? []).filter((m) => !state.measures.includes(m.key));
  return (
    <Section title={t("reports.builder.values")} testId="builder-values">
      {state.measures.length > 0 && (
        <ul className="space-y-1.5">
          {state.measures.map((key, i) => (
            <li key={key} className="flex items-center gap-1 rounded-md border border-hairline p-2" data-testid={`builder-values-item-${i}`}>
              <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink">{labelOf(key)}</span>
              <IconButton label={`${labelOf(key)}: ${t("reports.builder.moveUp")}`} onClick={() => dispatch({ type: "moveMeasure", index: i, delta: -1 })} disabled={i === 0} testId={`builder-values-up-${i}`}>
                ↑
              </IconButton>
              <IconButton label={`${labelOf(key)}: ${t("reports.builder.moveDown")}`} onClick={() => dispatch({ type: "moveMeasure", index: i, delta: 1 })} disabled={i === state.measures.length - 1} testId={`builder-values-down-${i}`}>
                ↓
              </IconButton>
              <IconButton label={`${labelOf(key)}: ${t("reports.builder.remove")}`} onClick={() => dispatch({ type: "removeMeasure", index: i })} testId={`builder-values-remove-${i}`}>
                ✕
              </IconButton>
            </li>
          ))}
        </ul>
      )}
      <AddMenu
        label={t("reports.builder.addMeasure")}
        items={available.map((m) => ({ value: m.key, label: t(m.label_key) }))}
        onPick={(key) => dispatch({ type: "addMeasure", key })}
        disabled={!source || state.measures.length >= MAX_MEASURES}
        testId="builder-values-add"
      />
    </Section>
  );
}

function TimeSection({ state, source, dispatch, issues }: { state: BuilderState; source: ReportSource; dispatch: Dispatch<BuilderAction>; issues: BuilderIssue[] }) {
  const { t } = useTranslation();
  const id = useId();
  const timeFields = source.dimensions.filter((d) => d.time_field);
  const active = presetOf(state.timeRange);
  const zones = useMemo(() => listTimeZones(), []);
  const range = state.timeRange?.kind === "absolute" ? state.timeRange : null;
  const invalid = issues.some((i) => i.key === "timeRangeInvalid" || i.key === "timeRangeTooLong");
  const tooLong = issues.some((i) => i.key === "timeRangeTooLong");
  const shownField = state.timeField ?? source.default_time_field ?? timeFields[0]?.key ?? null;

  const setCustom = (patch: { start?: string; end?: string }) => {
    const base = range ?? (() => {
      const r = dateRangeForPreset("last30Days");
      return { kind: "absolute" as const, start: r.from, end: r.to };
    })();
    dispatch({ type: "setTime", timeRange: { ...base, ...patch } });
  };

  return (
    <Section title={t("reports.builder.timeRange")} testId="builder-time">
      {timeFields.length > 1 && (
        <div className="space-y-1">
          <span className="block text-xs text-muted">{t("reports.builder.timeField")}</span>
          <SelectField
            items={timeFields.map((d) => ({ value: d.key, label: t(d.label_key) }))}
            value={shownField}
            onChange={(key) => dispatch({ type: "setTime", timeField: key === source.default_time_field ? null : key })}
            testId="builder-time-field"
            aria-label={t("reports.builder.timeField")}
          />
        </div>
      )}
      <div className="flex flex-wrap gap-1.5" role="group" aria-label={t("reports.builder.timeRange")}>
        <button
          type="button"
          className={cn(chipCls, state.timeRange === null && chipActiveCls)}
          aria-pressed={state.timeRange === null}
          onClick={() => dispatch({ type: "setTime", timeRange: null })}
          data-testid="builder-time-preset-none"
        >
          {t("reports.builder.timeNone")}
        </button>
        {BUILDER_PRESETS.map((p) => (
          <button
            key={p}
            type="button"
            className={cn(chipCls, active === p && chipActiveCls)}
            aria-pressed={active === p}
            onClick={() => dispatch({ type: "setTime", timeRange: presetRange(p) })}
            data-testid={`builder-time-preset-${p}`}
          >
            {t(`reports.presets.${p}`)}
          </button>
        ))}
        <button
          type="button"
          className={cn(chipCls, active === "custom" && chipActiveCls)}
          aria-pressed={active === "custom"}
          onClick={() => setCustom({})}
          data-testid="builder-time-preset-custom"
        >
          {t("reports.builder.timeCustom")}
        </button>
      </div>
      {state.timeRange?.kind === "absolute" && (
        <div className="grid grid-cols-2 gap-2">
          <label className="flex flex-col gap-1 text-xs text-muted">
            {t("reports.from")}
            <input
              type="date"
              className={inputCls}
              value={state.timeRange.start}
              max={state.timeRange.end || undefined}
              aria-invalid={invalid || undefined}
              onChange={(e) => setCustom({ start: e.target.value })}
              data-testid="builder-time-from"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted">
            {t("reports.to")}
            <input
              type="date"
              className={inputCls}
              value={state.timeRange.end}
              min={state.timeRange.start || undefined}
              aria-invalid={invalid || undefined}
              onChange={(e) => setCustom({ end: e.target.value })}
              data-testid="builder-time-to"
            />
          </label>
        </div>
      )}
      {tooLong && (
        <p className="text-xs text-danger" role="alert" data-testid="builder-time-too-long">
          {t("reports.builder.issues.timeRangeTooLong")}
        </p>
      )}
      <div className="space-y-1">
        <span id={`${id}-zone`} className="block text-xs text-muted">
          {t("reports.builder.timeZone")}
        </span>
        <SelectField<string>
          items={[{ value: "", label: t("reports.builder.timeZoneViewer") }, ...zones.map((z) => ({ value: z, label: z }))]}
          value={state.timeZone ?? ""}
          onChange={(zone) => dispatch({ type: "setTime", timeZone: zone === "" ? null : zone })}
          testId="builder-time-zone"
          aria-label={t("reports.builder.timeZone")}
        />
      </div>
    </Section>
  );
}

/** Left column of the builder: data basis, rows, columns, values, filter, time range and runtime parameters. */
export function BuilderFieldsPanel({
  state,
  dispatch,
  sources,
  source,
  issues,
}: {
  state: BuilderState;
  dispatch: Dispatch<BuilderAction>;
  sources: ReportSource[];
  source: ReportSource | undefined;
  issues: BuilderIssue[];
}) {
  const { t } = useTranslation();
  const fields = useMemo(() => (source ? fieldsFromSource(source, t) : []), [source, t]);
  const hasTime = (source?.dimensions ?? []).some((d) => d.time_field);
  return (
    <div className="space-y-5" data-testid="builder-fields">
      <Section title={t("reports.builder.dataBasis")}>
        <SelectField
          items={sources.map((s) => ({ value: s.key, label: t(s.label_key) }))}
          value={state.source}
          onChange={(key) => dispatch({ type: "setSource", source: key })}
          placeholder={t("reports.builder.dataBasisPlaceholder")}
          testId="builder-source"
          aria-label={t("reports.builder.dataBasis")}
        />
      </Section>
      <DimensionList target="rows" title={t("reports.builder.rows")} state={state} source={source} dispatch={dispatch} />
      <DimensionList target="columns" title={t("reports.builder.columns")} state={state} source={source} dispatch={dispatch} />
      <ValueList state={state} source={source} dispatch={dispatch} />
      <Section title={t("reports.builder.filter")} testId="builder-filter-section">
        <FilterBuilder
          fields={fields}
          value={state.filter}
          onChange={(filter) => dispatch({ type: "setFilter", filter })}
          testId="builder-filter"
          disabled={!source}
        />
      </Section>
      {source && hasTime && <TimeSection state={state} source={source} dispatch={dispatch} issues={issues} />}
      <Section title={t("reports.builder.parameters")} testId="builder-parameters-section">
        <BuilderParametersEditor
          parameters={state.parameters}
          fields={fields}
          onChange={(parameters) => dispatch({ type: "setParameters", parameters })}
          issues={issues}
        />
      </Section>
    </div>
  );
}
