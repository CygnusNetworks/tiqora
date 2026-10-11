import { useTranslation } from "react-i18next";
import type { WidgetProps } from "@/plugins/dashboardWidgets";
import { cn } from "@/lib/cn";
import { useWidgetData } from "../data";
import { WidgetFrame } from "../chrome/WidgetFrame";
import { WidgetDataBody } from "../chrome/WidgetDataBody";
import { Sparkline } from "../chrome/Sparkline";
import { ChooseSourceAction } from "./SourceAction";
import { FEWER_IS_BETTER, isCustomSource, presetOf, type TicketSource } from "./presets";

export interface CounterSettings {
  source: TicketSource;
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

/** KPI tile: label, change against the last earlier day with a sample (not always yesterday —
 * weekends and days without a visit have none), mono value and a 14-day sparkline. */
export function CounterWidget({ settings, size, mode }: WidgetProps<CounterSettings>) {
  const { t } = useTranslation();
  const { status, data } = useWidgetData<CounterData>();
  // Saved searches and own filters have no preset: the tile is named by its title override or the type.
  const preset = isCustomSource(settings.source) ? null : (presetOf(settings.source) ?? "mine_open");
  const tone = settings.tone in VALUE_TONE ? settings.tone : "neutral";
  const delta = status === "ok" && typeof data?.delta === "number" ? data.delta : null;
  const deltaGood = delta != null && delta < 0 && preset !== null && FEWER_IS_BETTER.has(preset);
  return (
    <WidgetFrame
      title={preset ? t(`dashboards.presets.${preset}.counter`) : t("dashboards.widgets.counter.title")}
      variant="kpi"
      mode={mode}
      testId="counter-widget"
      actions={
        delta != null && (
          <span
            title={t("dashboards.widgets.counter.delta", { delta: signed(delta) })}
            className={cn("min-w-0 truncate text-xs font-semibold", deltaGood ? "text-green" : "text-muted")}
          >
            {t("dashboards.widgets.counter.delta", { delta: signed(delta) })}
          </span>
        )
      }
    >
      <WidgetDataBody<CounterData> h={size.h} forbiddenAction={<ChooseSourceAction mode={mode} />}>
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
            <Sparkline points={Array.isArray(d?.spark) ? d.spark : []} tone={tone} />
          </div>
        )}
      </WidgetDataBody>
    </WidgetFrame>
  );
}
