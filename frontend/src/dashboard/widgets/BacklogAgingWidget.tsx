import { useTranslation } from "react-i18next";
import type { WidgetProps } from "@/plugins/dashboardWidgets";
import { ReportView } from "@/components/reports/ReportView";
import { ReportTileWidget, type TileRender } from "./ReportWidget";

export interface BacklogAgingSettings {
  max_rows: number;
}

/** Open tickets per queue, stacked by age bucket (a snapshot: the dashboard's time range does not apply). */
export function BacklogAgingWidget({ instanceId, settings, size, mode }: WidgetProps<BacklogAgingSettings>) {
  const { t } = useTranslation();
  const render: TileRender = ({ definition, result, tile, onSelectPoint, drillableDimensions }) => (
    <ReportView
      report={{ key: tile?.report?.key ?? "builtin.backlog_aging", definition }}
      result={result}
      compact
      onSelectPoint={onSelectPoint}
      drillableDimensions={drillableDimensions}
      testId="backlog-aging-view"
    />
  );
  return (
    <ReportTileWidget
      instanceId={instanceId}
      settings={{ ...settings, report_key: "builtin.backlog_aging" }}
      size={size}
      mode={mode}
      title={() => t("dashboards.widgets.backlogAging.title")}
      emptyText={t("dashboards.widgets.backlogAging.empty")}
      testId="backlog-aging-widget"
      render={render}
    />
  );
}
