import { useTranslation } from "react-i18next";
import type { ReportCell, ReportColumn, ReportResult } from "@/lib/reportsApi";
import { cellLabel, formatMeasure } from "./reportFormat";

/** Display text of one cell: dimension values get their labels, measures their unit. */
export function useCellText(result: ReportResult) {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? i18n.language ?? "de";
  return (column: ReportColumn, value: ReportCell): string =>
    column.role === "dimension"
      ? cellLabel(result, column.key, value, t, lang)
      : formatMeasure(value, column.type, lang);
}
