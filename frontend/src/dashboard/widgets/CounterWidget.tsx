import { useTranslation } from "react-i18next";
import type { WidgetProps } from "@/plugins/dashboardWidgets";
import { cn } from "@/lib/cn";
import { useWidgetData } from "../data";
import { WidgetFrame } from "../chrome/WidgetFrame";
import { WidgetDataBody } from "../chrome/WidgetDataBody";
import { Sparkline } from "../chrome/Sparkline";
import { FEWER_IS_BETTER, presetOf, type PresetSource } from "./presets";

export interface CounterSettings {
  source: PresetSource;
  tone: "neutral" | "danger" | "ok";
}

export interface CounterData {
  value: number;
  delta: number | null;
  spark: number[];
}

const VALUE_TONE = { neutral: "text-ink", danger: "text-danger", ok: "text-green" } as const;

/** "+2", "−1" (minus sign), "±0". */
function signed(n: number): string {
  if (n > 0) return `+${n}`;
  if (n < 0) return `−${Math.abs(n)}`;
  return "±0";
}

/** KPI tile: label, change since the last day with a sample, mono value and a 14-day sparkline. */
export function CounterWidget({ settings, size, mode }: WidgetProps<CounterSettings>) {
  const { t } = useTranslation();
  const { status, data } = useWidgetData<CounterData>();
  const preset = presetOf(settings.source) ?? "mine_open";
  const tone = settings.tone in VALUE_TONE ? settings.tone : "neutral";
  const delta = status === "ok" && typeof data?.delta === "number" ? data.delta : null;
  const deltaGood = delta != null && delta < 0 && FEWER_IS_BETTER.has(preset);
  return (
    <WidgetFrame
      title={t(`dashboards.presets.${preset}.counter`)}
      variant="kpi"
      mode={mode}
      testId="counter-widget"
      actions={
        delta != null && (
          <span className={cn("shrink-0 text-xs font-semibold", deltaGood ? "text-green" : "text-muted")}>
            {t("dashboards.widgets.counter.delta", { delta: signed(delta) })}
          </span>
        )
      }
    >
      <WidgetDataBody<CounterData> h={size.h}>
        {(d) => (
          <div className="flex h-full items-end justify-between gap-3">
            <span
              className={cn(
                "font-mono text-[30px] font-semibold leading-none tracking-tight tabular-nums",
                VALUE_TONE[tone],
              )}
            >
              {typeof d?.value === "number" ? d.value : "—"}
            </span>
            <Sparkline points={Array.isArray(d?.spark) ? d.spark : []} tone={tone === "neutral" ? "neutral" : tone} />
          </div>
        )}
      </WidgetDataBody>
    </WidgetFrame>
  );
}
