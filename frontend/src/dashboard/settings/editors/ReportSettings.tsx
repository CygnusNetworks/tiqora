import { useId, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ReportParameterControls } from "@/components/reports/ReportParameterControls";
import { labelKeysOf, usePluginReportTexts } from "@/components/reports/usePluginReportTexts";
import { choiceIndex, hasTimeRange, runtimeParameters, type ReportParamValues } from "@/components/reports/reportFilter";
import { isBuiltinReport, reportTitle, reportsApi, type ReportItem } from "@/lib/reportsApi";
import { TIME_RANGE_PRESETS, timeRangeLabelKey } from "../../filters/timeRanges";
import type { SettingsEditorProps } from "../registry";
import { FIELD } from "./SourceFields";

const DISPLAYS = ["auto", "table"] as const;

function withoutKey<T extends Record<string, unknown>>(value: T, key: string): Record<string, unknown> {
  return Object.fromEntries(Object.entries(value).filter(([k]) => k !== key));
}

const Err = ({ message, id }: { message?: string; id: string }) =>
  message ? (
    <p id={id} role="alert" className="mt-1 text-xs text-danger">
      {message}
    </p>
  ) : null;

/**
 * Report widget: which report (built-in or saved, as the viewer sees them), its own time range,
 * the report's runtime parameters, the split-by choice, the display and the number of rows.
 */
export function ReportSettingsEditor({ value, onChange, errors }: SettingsEditorProps) {
  const { t } = useTranslation();
  const uid = useId();
  const catalogue = useQuery({
    queryKey: ["reports", "catalogue"],
    queryFn: ({ signal }) => reportsApi.catalogue(signal),
    staleTime: 60_000,
  });
  const reports = catalogue.data?.reports ?? [];
  const reportKey = typeof value.report_key === "string" ? value.report_key : "";
  const chosen: ReportItem | undefined = reports.find((r) => r.key === reportKey);
  const definition = chosen?.definition ?? undefined;
  const hasParams = definition ? runtimeParameters(definition).length > 0 : false;
  const sources = useQuery({
    queryKey: ["reports", "sources"],
    queryFn: ({ signal }) => reportsApi.sources(signal),
    enabled: hasParams,
  });
  const source = sources.data?.find((s) => s.key === definition?.source);
  usePluginReportTexts(reports, useMemo(() => labelKeysOf(source), [source]));

  const builtin = reports.filter((r) => isBuiltinReport(r));
  const saved = reports.filter((r) => r.kind === "saved" && r.definition != null);
  const timeRange = typeof value.time_range === "string" ? value.time_range : "";
  const display = (DISPLAYS as readonly unknown[]).includes(value.display) ? String(value.display) : "auto";
  const maxRows = typeof value.max_rows === "number" ? value.max_rows : 10;
  const parameters = (value.parameters && typeof value.parameters === "object" ? value.parameters : {}) as ReportParamValues;
  const choices = (value.dimension_choices && typeof value.dimension_choices === "object" ? value.dimension_choices : {}) as Record<string, string>;
  const at = definition ? choiceIndex(definition) : -1;
  const choiceDim = definition && at >= 0 ? definition.dimensions?.[at] : undefined;

  const pickReport = (key: string) =>
    // Parameters and the split-by choice belong to one report: a new report starts clean.
    onChange({ ...value, report_key: key, parameters: {}, dimension_choices: {} });
  const setParameters = (next: ReportParamValues) => {
    const kept = Object.fromEntries(Object.entries(next).filter(([, v]) => v === true || (Array.isArray(v) && v.length > 0)));
    onChange({ ...value, parameters: kept });
  };

  const label = "mb-1 block text-xs font-medium text-muted";
  return (
    <div className="space-y-5" data-testid="report-settings">
      <div>
        <label htmlFor={`${uid}-report`} className={label}>
          {t("dashboards.settings.report.report")}
        </label>
        <select
          id={`${uid}-report`}
          value={chosen ? reportKey : ""}
          onChange={(e) => pickReport(e.target.value)}
          aria-describedby={errors.report_key ? `${uid}-report-err` : undefined}
          aria-invalid={errors.report_key ? true : undefined}
          className={FIELD}
          data-testid="report-settings-report"
        >
          <option value="" disabled>
            {catalogue.isPending ? t("dashboards.state.loading") : t("dashboards.settings.report.choose")}
          </option>
          {builtin.length > 0 && (
            <optgroup label={t("dashboards.settings.report.builtin")}>
              {builtin.map((r) => (
                <option key={r.key} value={r.key}>
                  {reportTitle(r, t)}
                </option>
              ))}
            </optgroup>
          )}
          {saved.length > 0 && (
            <optgroup label={t("dashboards.settings.report.saved")}>
              {saved.map((r) => (
                <option key={r.key} value={r.key}>
                  {reportTitle(r, t)}
                </option>
              ))}
            </optgroup>
          )}
        </select>
        {catalogue.isError && <p className="mt-1 text-xs text-danger">{t("dashboards.settings.report.catalogueFailed")}</p>}
        <Err id={`${uid}-report-err`} message={errors.report_key} />
      </div>

      {(!definition || hasTimeRange(definition)) && (
        <div>
          <label htmlFor={`${uid}-range`} className={label}>
            {t("dashboards.filters.timeRange")}
          </label>
          <select
            id={`${uid}-range`}
            value={timeRange}
            onChange={(e) => onChange(e.target.value === "" ? withoutKey(value, "time_range") : { ...value, time_range: e.target.value })}
            className={FIELD}
          >
            <option value="">{t("dashboards.filters.asReport")}</option>
            {TIME_RANGE_PRESETS.map((p) => (
              <option key={p} value={p}>
                {t(timeRangeLabelKey(p))}
              </option>
            ))}
          </select>
          <Err id={`${uid}-range-err`} message={errors.time_range} />
        </div>
      )}

      {definition && hasParams && (
        <div data-testid="report-settings-params">
          <ReportParameterControls
            definition={definition}
            source={source}
            values={parameters}
            onChange={setParameters}
            testId="report-settings-param"
          />
          <Err id={`${uid}-params-err`} message={errors.parameters} />
        </div>
      )}

      {choiceDim && (
        <div>
          <label htmlFor={`${uid}-split`} className={label}>
            {t("dashboards.settings.report.splitBy")}
          </label>
          <select
            id={`${uid}-split`}
            value={choices[String(at)] ?? ""}
            onChange={(e) =>
              onChange({ ...value, dimension_choices: e.target.value === "" ? {} : { [String(at)]: e.target.value } })
            }
            className={FIELD}
          >
            <option value="">{t("dashboards.filters.asReport")}</option>
            {[choiceDim.key, ...(choiceDim.choices ?? [])].map((k) => (
              <option key={k} value={k}>
                {t(`reports.dimensions.${k}`, { defaultValue: k })}
              </option>
            ))}
          </select>
        </div>
      )}

      <div className="grid gap-5 sm:grid-cols-2">
        <div>
          <label htmlFor={`${uid}-display`} className={label}>
            {t("dashboards.settings.report.display")}
          </label>
          <select
            id={`${uid}-display`}
            value={display}
            onChange={(e) => onChange({ ...value, display: e.target.value })}
            className={FIELD}
          >
            <option value="auto">{t("dashboards.filters.asReport")}</option>
            <option value="table">{t("dashboards.settings.report.table")}</option>
          </select>
        </div>
        <div>
          <label htmlFor={`${uid}-rows`} className={label}>
            {t("dashboards.settings.report.rows")}
          </label>
          <input
            id={`${uid}-rows`}
            type="number"
            min={1}
            max={50}
            value={maxRows}
            onChange={(e) => onChange({ ...value, max_rows: e.target.value === "" ? "" : Number(e.target.value) })}
            aria-invalid={errors.max_rows ? true : undefined}
            className={FIELD}
          />
          <Err id={`${uid}-rows-err`} message={errors.max_rows} />
        </div>
      </div>
    </div>
  );
}
