import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import type { WidgetProps } from "@/plugins/dashboardWidgets";
import { cn } from "@/lib/cn";
import { useWidgetData } from "../data";
import { WidgetFrame, useWidgetChrome } from "../chrome/WidgetFrame";
import { useDrill } from "../drill/useDrill";
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
export function CounterWidget({ instanceId, settings, size, mode }: WidgetProps<CounterSettings>) {
  const { t } = useTranslation();
  const drill = useDrill(instanceId, mode);
  const chrome = useWidgetChrome();
  const { status, data } = useWidgetData<CounterData>();
  // Saved searches and own filters have no preset: the tile is named by its title override or the type.
  const preset = isCustomSource(settings.source) ? null : (presetOf(settings.source) ?? "mine_open");
  const tone = settings.tone in VALUE_TONE ? settings.tone : "neutral";
  const delta = status === "ok" && typeof data?.delta === "number" ? data.delta : null;
  const title = preset ? t(`dashboards.presets.${preset}.counter`) : t("dashboards.widgets.counter.title");
  const deltaGood = delta != null && delta < 0 && preset !== null && FEWER_IS_BETTER.has(preset);
  if (mode === "wallboard")
    return (
      <WidgetFrame title={title} variant="kpi" mode={mode} testId="counter-widget" scaleBody={false}>
        <WidgetDataBody<CounterData> h={size.h}>
          {(d) => (
            <div className="flex h-full flex-col justify-end gap-[0.4em]" data-testid="counter-wallboard">
              {drill && (
                <Link
                  {...drill()}
                  aria-label={t("dashboards.drill.title", { title: chrome.titleOverride?.trim() || title })}
                  className="absolute inset-0 rounded-[20px]"
                  data-testid="counter-drill"
                />
              )}
              <span
                data-testid="counter-value"
                className={cn(
                  "font-mono text-[length:calc(96px*var(--wb-zoom,1))] font-semibold leading-none tracking-tight tabular-nums",
                  VALUE_TONE[tone],
                )}
              >
                {typeof d?.value === "number" ? d.value : "—"}
              </span>
              {delta != null && (
                <span
                  data-testid="counter-sub"
                  className={cn(
                    "text-[length:calc(20px*var(--wb-zoom,1))] leading-tight",
                    deltaGood ? "text-green" : "text-muted",
                  )}
                >
                  {t("dashboards.widgets.counter.delta", { delta: signed(delta) })}
                </span>
              )}
            </div>
          )}
        </WidgetDataBody>
      </WidgetFrame>
    );
  return (
    <WidgetFrame
      title={title}
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
            {drill && (
              // The whole tile is the link: stretched over the card (the frame is its positioned ancestor).
              <Link
                {...drill()}
                aria-label={t("dashboards.drill.title", { title: chrome.titleOverride?.trim() || title })}
                className="absolute inset-0 rounded-[14px] focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
                data-testid="counter-drill"
              />
            )}
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
