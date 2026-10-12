import { useTranslation } from "react-i18next";
import type { WidgetProps } from "@/plugins/dashboardWidgets";
import { ReportView } from "@/components/reports/ReportView";
import { ReportTileWidget, type TileRender } from "./ReportWidget";

export interface HeatmapSettings {
  time_range: "30d" | "90d";
}

/** Created tickets by weekday (Monday first) and hour of day. */
export function HeatmapWidget({ instanceId, settings, size, mode }: WidgetProps<HeatmapSettings>) {
  const { t } = useTranslation();
  const render: TileRender = ({ definition, result, tile }) => (
    <ReportView
      report={{ key: tile?.report?.key ?? "builtin.created_heatmap", definition }}
      result={result}
      compact
      testId="heatmap-view"
    />
  );
  return (
    <ReportTileWidget
      instanceId={instanceId}
      settings={{ ...settings, report_key: "builtin.created_heatmap" }}
      size={size}
      mode={mode}
      title={() => t("dashboards.widgets.heatmap.title")}
      emptyText={t("dashboards.widgets.heatmap.empty")}
      testId="heatmap-widget"
      render={render}
    />
  );
}
