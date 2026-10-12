import { useTranslation } from "react-i18next";
import type { WidgetProps } from "@/plugins/dashboardWidgets";
import { GaugeChart } from "@/components/agent/stats/GaugeChart";
import { gaugeFigures } from "./gaugeFigures";
import { ReportTileWidget, type ReportTileData, type TileRender } from "./ReportWidget";

export interface SlaGaugeSettings {
  time_range: string;
  target_percent: number;
}

/** What the SLA gauge provider adds to the report tile. */
export interface SlaGaugeData extends ReportTileData {
  /** Fulfilment in percent (one decimal) or null when no ticket had an SLA target. */
  rate?: number | null;
  tickets?: number;
  target?: number;
}

/** Share of tickets that met their SLA in the period, as a semicircle gauge against the target. */
export function SlaGaugeWidget({ instanceId, settings, size, mode }: WidgetProps<SlaGaugeSettings>) {
  const { t } = useTranslation();
  const render: TileRender = ({ result, tile, fromJob }) => {
    const served = tile as SlaGaugeData | undefined;
    const { rate, tickets } = fromJob || served?.rate === undefined ? gaugeFigures(result) : { rate: served.rate, tickets: served.tickets ?? 0 };
    const target = served?.target ?? settings.target_percent;
    return (
      <div className="flex h-full min-h-0 flex-col items-center justify-center gap-1" data-testid="sla-gauge-body">
        <GaugeChart
          value={rate}
          target={target}
          label={t("dashboards.widgets.slaGauge.label")}
          emptyLabel={t("dashboards.widgets.slaGauge.noTickets")}
          testId="sla-gauge"
        />
        {rate !== null && (
          <p className="text-xs text-muted" data-testid="sla-gauge-tickets">
            {t("dashboards.widgets.slaGauge.tickets", { count: tickets })}
          </p>
        )}
      </div>
    );
  };
  return (
    <ReportTileWidget
      instanceId={instanceId}
      settings={settings as unknown as Record<string, unknown>}
      size={size}
      mode={mode}
      title={() => t("dashboards.widgets.slaGauge.title")}
      emptyText={t("dashboards.widgets.slaGauge.noTickets")}
      testId="sla-gauge-widget"
      render={render}
    />
  );
}
