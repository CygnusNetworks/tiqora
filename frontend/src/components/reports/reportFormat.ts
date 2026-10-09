import type { TFunction } from "i18next";
import i18n from "@/i18n";
import { toBcp47 } from "@/i18n/locales";
import { formatCalendarDay } from "@/lib/format";
import type {
  ReportCell,
  ReportColumn,
  ReportDefinition,
  ReportExportIn,
  ReportResult,
} from "@/lib/reportsApi";

/** Server key of the folded-away tail of a `top_n` dimension. */
export const OTHER_KEY = "__other__";
const DASH = "—";
/** The export accepts at most this many labelled values per column. */
const MAX_EXPORT_VALUES = 500;

type ColumnType = ReportColumn["type"];

function currentLocale(): string {
  return i18n.resolvedLanguage ?? i18n.language ?? "de";
}

/** ISO 8601 week number of a calendar day (`YYYY-MM-DD`), computed in UTC. */
export function isoWeekNumber(ymd: string): number | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(ymd);
  if (!m) return null;
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  const day = d.getUTCDay() || 7;
  d.setUTCDate(d.getUTCDate() + 4 - day); // Thursday of this ISO week
  const yearStart = Date.UTC(d.getUTCFullYear(), 0, 1);
  return Math.ceil(((d.getTime() - yearStart) / 86_400_000 + 1) / 7);
}

function dateBucketLabel(
  value: string,
  grain: ReportColumn["grain"] | undefined,
  t: TFunction,
  locale: string,
): string {
  const bcp = toBcp47(locale);
  if (grain === "week") {
    const week = isoWeekNumber(value);
    return week == null ? value : t("reports.weekLabel", { week });
  }
  if (grain === "month") return formatCalendarDay(value, bcp, { month: "long", year: "numeric" });
  if (grain === "quarter") {
    const m = /^(\d{4})-(\d{2})/.exec(value);
    return m ? `Q${Math.floor((Number(m[2]) - 1) / 3) + 1} ${m[1]}` : value;
  }
  if (grain === "year") return formatCalendarDay(value, bcp, { year: "numeric" });
  const label = formatCalendarDay(value, bcp);
  return label === DASH ? value : label;
}

/**
 * Display text of a dimension value: server labels, then the enum texts
 * (`i18n_values`), the "Weitere" bucket, then date / hour / weekday
 * formatting, else the plain value.
 */
export function cellLabel(
  result: ReportResult,
  columnKey: string,
  value: ReportCell,
  t: TFunction,
  locale: string = currentLocale(),
): string {
  if (value === null || value === undefined) return DASH;
  const text = String(value);
  const own = result.labels?.[columnKey]?.[text];
  if (own !== undefined) return own;
  if (text === OTHER_KEY) return t("reports.other");
  const column = result.columns.find((c) => c.key === columnKey);
  if (column?.i18n_values) {
    return t(`${column.i18n_values}.${text}`, { defaultValue: text });
  }
  switch (column?.type) {
    case "date":
      return dateBucketLabel(text, column.grain, t, locale);
    case "hour": {
      const n = Number(text);
      return Number.isFinite(n) ? t("reports.hourLabel", { hour: String(n).padStart(2, "0") }) : text;
    }
    case "weekday":
      return t(`reports.values.weekday.${text}`, { defaultValue: text });
    default:
      return text;
  }
}

function dayUnit(locale: string): string {
  return toBcp47(locale).toLowerCase().startsWith("de") ? "T" : "d";
}

function formatNumber(value: number, locale: string, fractionDigits: [number, number]): string {
  return new Intl.NumberFormat(toBcp47(locale), {
    minimumFractionDigits: fractionDigits[0],
    maximumFractionDigits: fractionDigits[1],
  }).format(value);
}

const round1 = (n: number) => Math.round(n * 10) / 10;

/**
 * Minutes as "45 min" / "3,5 h" / "2,1 T". Each unit is chosen from the value
 * rounded to one decimal, so 59.96 min reads "1 h", never "60 min", and
 * 1439.9 min reads "1 T", never "24 h".
 */
function formatMinutes(minutes: number, locale: string): string {
  if (Math.abs(round1(minutes)) < 60) return `${formatNumber(minutes, locale, [0, 1])} min`;
  const hours = minutes / 60;
  if (Math.abs(round1(hours)) < 24) return `${formatNumber(hours, locale, [0, 1])} h`;
  return `${formatNumber(minutes / 1440, locale, [0, 1])} ${dayUnit(locale)}`;
}

/** Formats a measure cell by its column type; `null` is "—". */
export function formatMeasure(value: ReportCell, type: ColumnType, locale: string): string {
  if (value === null || value === undefined) return DASH;
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return String(value);
  switch (type) {
    case "percent":
      return `${formatNumber(n * 100, locale, [0, 1])} %`;
    case "minutes":
      return formatMinutes(n, locale);
    case "hours":
      return formatMinutes(n * 60, locale);
    case "days":
      return `${formatNumber(n, locale, [0, 1])} ${dayUnit(locale)}`;
    case "units":
      return formatNumber(n, locale, [2, 2]);
    case "number":
      return formatNumber(n, locale, [0, 2]);
    case "count":
      return formatNumber(n, locale, [0, 0]);
    default:
      return String(value);
  }
}

export type SeriesData = { key: string; label: string; values: (number | null)[] };

function num(v: ReportCell | undefined): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/**
 * Pivots the rows for a chart. Categories are the distinct values of the
 * non-series dimension (or the first dimension); series are the values of the
 * `series_dimension`, or one series per plotted measure when it is null.
 */
export function toSeries(
  result: ReportResult,
  viz: ReportDefinition["visualization"],
  t: TFunction,
): { categories: { key: string; label: string }[]; series: SeriesData[] } {
  const dims = result.columns.flatMap((c, i) => (c.role === "dimension" ? [i] : []));
  const measures = result.columns.flatMap((c, i) => (c.role === "measure" ? [i] : []));
  const plotted = (viz?.chart_measures ?? null)?.map((m) => measures[m]).filter((i) => i != null);
  const plottedMeasures = plotted && plotted.length > 0 ? plotted : measures;

  const seriesIdx = viz?.series_dimension != null ? dims[viz.series_dimension] : undefined;
  const categoryIdx = dims.find((i) => i !== seriesIdx);

  const categoryKeys: string[] = [];
  const categorySeen = new Set<string>();
  const catKey = (row: ReportCell[]) => (categoryIdx === undefined ? "" : String(row[categoryIdx]));
  for (const row of result.rows) {
    const k = catKey(row);
    if (!categorySeen.has(k)) {
      categorySeen.add(k);
      categoryKeys.push(k);
    }
  }
  const categories =
    categoryIdx === undefined
      ? []
      : categoryKeys.map((key) => ({
          key,
          label: cellLabel(result, result.columns[categoryIdx].key, key, t),
        }));
  if (categoryIdx === undefined) {
    // No category dimension: the rows themselves are the (single) category.
    return { categories: [], series: [] };
  }
  const position = new Map(categoryKeys.map((k, i) => [k, i]));

  if (seriesIdx !== undefined) {
    const measure = plottedMeasures[0];
    const seriesKeys: string[] = [];
    const values = new Map<string, (number | null)[]>();
    for (const row of result.rows) {
      const sk = String(row[seriesIdx]);
      let arr = values.get(sk);
      if (!arr) {
        arr = categoryKeys.map(() => null);
        values.set(sk, arr);
        seriesKeys.push(sk);
      }
      const v = measure === undefined ? null : num(row[measure]);
      const at = position.get(catKey(row)) as number;
      arr[at] = v === null ? arr[at] : (arr[at] ?? 0) + v;
    }
    return {
      categories,
      series: seriesKeys.map((key) => ({
        key,
        label: cellLabel(result, result.columns[seriesIdx].key, key, t),
        values: values.get(key) as (number | null)[],
      })),
    };
  }

  return {
    categories,
    series: plottedMeasures.map((mi) => {
      const col = result.columns[mi];
      const values: (number | null)[] = categoryKeys.map(() => null);
      for (const row of result.rows) {
        const v = num(row[mi]);
        const at = position.get(catKey(row)) as number;
        if (v !== null) values[at] = (values[at] ?? 0) + v;
      }
      return { key: col.key, label: t(col.label_key, { defaultValue: col.key }), values };
    }),
  };
}

/**
 * Translated headers and enum/id values for `POST /reports/export` (the
 * server writes no UI texts). Date columns stay real dates; only the
 * "Weitere" bucket is labelled there.
 */
export function exportLabels(
  result: ReportResult,
  t: TFunction,
): Pick<ReportExportIn, "column_labels" | "value_labels" | "totals_label"> {
  const column_labels: Record<string, string> = {};
  const value_labels: Record<string, Record<string, string>> = {};
  result.columns.forEach((col, idx) => {
    column_labels[col.key] = t(col.label_key, { defaultValue: col.key });
    if (col.role !== "dimension") return;
    const map: Record<string, string> = {};
    for (const row of result.rows) {
      const cell = row[idx];
      if (cell === null || cell === undefined) continue;
      const text = String(cell);
      if (text in map) continue;
      if (col.type === "date" && text !== OTHER_KEY) continue;
      if (Object.keys(map).length >= MAX_EXPORT_VALUES) break;
      map[text] = cellLabel(result, col.key, cell, t);
    }
    if (Object.keys(map).length > 0) value_labels[col.key] = map;
  });
  return { column_labels, value_labels, totals_label: t("reports.totals") };
}
