import { useTranslation } from "react-i18next";
import { RouterAnchor } from "@/components/ui/RouterAnchor";
import { cn } from "@/lib/cn";
import type { ReportCell, ReportColumn, ReportResult } from "@/lib/reportsApi";
import { ticketHref } from "./reportFormat";
import { useCellText } from "./useCellText";

const linkCls =
  "text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";

const tableHeadCls =
  "sticky top-0 z-10 border-b border-hairline bg-surface px-3 py-2 text-xs font-medium text-muted";

/** Flat result table: one row per result row, measures right-aligned, grand total in the footer. */
export function ReportTable({ result, testId = "report-table" }: { result: ReportResult; testId?: string }) {
  const { t } = useTranslation();
  const text = useCellText(result);
  const grand = result.totals?.grand ?? null;
  const isMeasure = (c: ReportColumn) => c.role === "measure";
  const labelAt = result.columns.findIndex((c) => c.role === "dimension");
  // Ticket cells open the ticket; every other cell is plain text.
  const cell = (c: ReportColumn, value: ReportCell) => {
    const href = ticketHref(c, value);
    return href ? (
      <RouterAnchor href={href} className={linkCls}>
        {text(c, value)}
      </RouterAnchor>
    ) : (
      text(c, value)
    );
  };

  return (
    <div className="max-h-[32rem] overflow-auto rounded-lg border border-hairline bg-surface" data-testid={testId}>
      <table className="w-full border-separate border-spacing-0 text-sm">
        <thead>
          <tr>
            {result.columns.map((c) => (
              <th key={c.key} scope="col" className={cn(tableHeadCls, isMeasure(c) ? "text-right" : "text-left")}>
                {t(c.label_key, { defaultValue: c.key })}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {result.rows.map((row, ri) => (
            <tr key={ri} className="hover:bg-surface-subtle" data-testid={`${testId}-row-${ri}`}>
              {result.columns.map((c, ci) => (
                <td
                  key={c.key}
                  className={cn(
                    "border-b border-hairline px-3 py-1.5",
                    isMeasure(c) ? "text-right font-mono tabular-nums text-ink" : "text-ink",
                  )}
                >
                  {cell(c, row[ci])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
        {grand && (
          <tfoot>
            <tr data-testid={`${testId}-totals`}>
              {result.columns.map((c, ci) => (
                <td
                  key={c.key}
                  className={cn(
                    "sticky bottom-0 border-t border-hairline bg-surface px-3 py-1.5 font-semibold",
                    isMeasure(c) ? "text-right font-mono tabular-nums text-ink" : "text-ink",
                  )}
                >
                  {ci === labelAt ? t("reports.totals") : isMeasure(c) ? text(c, grand[ci] ?? null) : ""}
                </td>
              ))}
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  );
}
