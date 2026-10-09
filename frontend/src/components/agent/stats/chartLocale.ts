import { useTranslation } from "react-i18next";
import { formatMeasure } from "@/components/reports/reportFormat";

/** Translation plus the app-locale number formatters used by the charts. */
export function useChartFormat() {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? i18n.language ?? "de";
  return {
    t,
    /** 0.125 -> "12,5 %" (de). */
    percent: (fraction: number) => formatMeasure(fraction, "percent", lang),
    /** Plain number in the app locale (0 to 2 decimals). */
    number: (v: number) => formatMeasure(v, "number", lang),
  };
}
