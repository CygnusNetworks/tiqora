import { useTranslation } from "react-i18next";
import { cn } from "@/lib/cn";
import type { ReportCell, ReportColumn, ReportResult } from "@/lib/reportsApi";
import { ReportTable } from "./ReportTable";
import { useCellText } from "./useCellText";

/** Measure types whose totals may be summed when the server sent none. */
const ADDITIVE = new Set<ReportColumn["type"]>(["count", "units"]);

function distinct(result: ReportResult, at: number): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const row of result.rows) {
    const k = String(row[at]);
    if (!seen.has(k)) {
      seen.add(k);
      out.push(k);
    }
  }
  // Calendar buckets read left to right in time order; "Weitere" stays last.
  if (result.columns[at]?.type === "date") out.sort((a, b) => (a === "__other__" ? 1 : b === "__other__" ? -1 : a < b ? -1 : a > b ? 1 : 0));
  return out;
}

function sum(values: (number | null)[]): number | null {
  const nums = values.filter((v): v is number => typeof v === "number");
  return nums.length === 0 ? null : nums.reduce((a, b) => a + b, 0);
}

const cellCls = "border-b border-hairline px-3 py-1.5 text-right font-mono tabular-nums";
const totalCls = "bg-surface-subtle font-semibold text-ink";

/**
 * Cross table of two dimensions: the second dimension's values across the top,
 * the first's down the left, one measure in the cells, totals in the last
 * column and row. The header row and the first column stay in view while
 * scrolling. Falls back to the flat table with fewer than two dimensions.
 */
export function PivotTable({
  result,
  measureIndex,
  stock = false,
  testId = "pivot",
}: {
  result: ReportResult;
  /** Stock measure (a day's state): never summed over a date dimension. */
  stock?: boolean;
  /** Column index of the measure to show; default the first measure. */
  measureIndex?: number;
  testId?: string;
}) {
  const { t } = useTranslation();
  const text = useCellText(result);
  const dims = result.columns.flatMap((c, i) => (c.role === "dimension" ? [i] : []));
  const mi = measureIndex ?? result.columns.findIndex((c) => c.role === "measure");
  if (dims.length < 2 || mi < 0) return <ReportTable result={result} testId={testId} />;

  const [ri, ci] = dims;
  const rowDim = result.columns[ri];
  const colDim = result.columns[ci];
  const measure = result.columns[mi];
  const rowKeys = distinct(result, ri);
  const colKeys = distinct(result, ci);
  const cells = new Map<string, ReportCell>();
  for (const row of result.rows) cells.set(`${String(row[ri])}\u0000${String(row[ci])}`, row[mi]);
  const value = (r: string, c: string): number | null => {
    const v = cells.get(`${r}\u0000${c}`);
    return typeof v === "number" ? v : null;
  };

  const additive = ADDITIVE.has(measure.type);
  // Summing a stock over days is meaningless; over queues/states on one day it is the total.
  const sumOverCols = additive && !(stock && colDim.type === "date");
  const sumOverRows = additive && !(stock && rowDim.type === "date");
  const rowTotal = (r: string): ReportCell => {
    const server = result.totals?.rows?.[r];
    if (server) return server[mi] ?? null;
    return sumOverCols ? sum(colKeys.map((c) => value(r, c))) : null;
  };
  const colTotal = (c: string): ReportCell => {
    const server = result.totals?.columns?.[c];
    if (server) return server[mi] ?? null;
    return sumOverRows ? sum(rowKeys.map((r) => value(r, c))) : null;
  };
  const grand: ReportCell =
    result.totals?.grand?.[mi] ?? (sumOverCols && sumOverRows ? sum(rowKeys.flatMap((r) => colKeys.map((c) => value(r, c)))) : null);

  const rowTotals = new Map(rowKeys.map((r) => [r, rowTotal(r)]));
  const colTotals = new Map(colKeys.map((c) => [c, colTotal(c)]));
  // A totals column/row with nothing to show (e.g. a stock over days) is left out.
  const showRowTotals = grand != null || [...rowTotals.values()].some((v) => v != null);
  const showColTotals = grand != null || [...colTotals.values()].some((v) => v != null);

  return (
    <div className="max-h-[32rem] overflow-auto rounded-lg border border-hairline bg-surface" data-testid={testId}>
      <table className="border-separate border-spacing-0 text-sm">
        <caption className="sr-only">
          {t(measure.label_key, { defaultValue: measure.key })}: {t(rowDim.label_key, { defaultValue: rowDim.key })} ×{" "}
          {t(colDim.label_key, { defaultValue: colDim.key })}
        </caption>
        <thead className="sticky top-0 z-20 bg-surface" data-testid={`${testId}-head`}>
          <tr>
            <th
              scope="col"
              className="sticky left-0 z-30 border-b border-r border-hairline bg-surface px-3 py-2 text-left text-xs font-medium text-muted"
            >
              {t(rowDim.label_key, { defaultValue: rowDim.key })}
            </th>
            {colKeys.map((c) => (
              <th
                key={c}
                scope="col"
                className="whitespace-nowrap border-b border-hairline bg-surface px-3 py-2 text-right text-xs font-medium text-muted"
                data-testid={`${testId}-col-${c}`}
              >
                {text(colDim, c)}
              </th>
            ))}
            {showRowTotals && (
            <th scope="col" className="border-b border-l border-hairline bg-surface px-3 py-2 text-right text-xs font-semibold text-ink">
              {t("reports.totals")}
            </th>
            )}
          </tr>
        </thead>
        <tbody>
          {rowKeys.map((r) => (
            <tr key={r} className="hover:bg-surface-subtle">
              <th
                scope="row"
                className="sticky left-0 z-10 max-w-[16rem] truncate border-b border-r border-hairline bg-surface px-3 py-1.5 text-left font-normal text-ink"
                title={text(rowDim, r)}
                data-testid={`${testId}-row-${r}`}
              >
                {text(rowDim, r)}
              </th>
              {colKeys.map((c) => (
                <td key={c} className={cn(cellCls, "text-ink")} data-testid={`${testId}-cell-${r}-${c}`}>
                  {text(measure, value(r, c))}
                </td>
              ))}
              {showRowTotals && (
                <td className={cn(cellCls, totalCls, "border-l border-hairline")} data-testid={`${testId}-rowtotal-${r}`}>
                  {text(measure, rowTotals.get(r) ?? null)}
                </td>
              )}
            </tr>
          ))}
        </tbody>
        {showColTotals && (
        <tfoot>
          <tr>
            <th
              scope="row"
              className="sticky bottom-0 left-0 z-10 border-r border-t border-hairline bg-surface-subtle px-3 py-1.5 text-left font-semibold text-ink"
            >
              {t("reports.totals")}
            </th>
            {colKeys.map((c) => (
              <td
                key={c}
                className={cn(cellCls, totalCls, "sticky bottom-0 border-t")}
                data-testid={`${testId}-coltotal-${c}`}
              >
                {text(measure, colTotals.get(c) ?? null)}
              </td>
            ))}
            {showRowTotals && (
              <td className={cn(cellCls, totalCls, "sticky bottom-0 border-l border-t")} data-testid={`${testId}-grand`}>
                {text(measure, grand)}
              </td>
            )}
          </tr>
        </tfoot>
        )}
      </table>
    </div>
  );
}
