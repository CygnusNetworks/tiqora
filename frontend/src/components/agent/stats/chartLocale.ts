import { useTranslation } from "react-i18next";
import { formatMeasure } from "@/components/reports/reportFormat";
import { toBcp47 } from "@/i18n/locales";

/** Translation plus the app-locale number formatters used by the charts. */
export function useChartFormat() {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? i18n.language ?? "de";
  return {
    t,
    /** 0.125 -> "12,5 %" (de). */
    percent: (fraction: number) => formatMeasure(fraction, "percent", lang),
    /** 0.02 (a difference of two rates) -> "2,0" percentage points (de), one decimal. */
    points: (fraction: number) =>
      new Intl.NumberFormat(toBcp47(lang), { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(fraction * 100),
    /** Plain number in the app locale (0 to 2 decimals). */
    number: (v: number) => formatMeasure(v, "number", lang),
  };
}
