import type { WidgetMode } from "@/plugins/dashboardWidgets";
import { useWidgetChrome } from "../chrome/WidgetFrame";
import { useIsKiosk } from "../wallboard/kioskContext";
import { canDrill, drillHref, type DrillPoint } from "./drillLinks";

/**
 * The drill link builder of a placed widget, or null where it has none: edit and preview render the
 * widget inert, and a widget outside a stored dashboard (no `dashboardId`) has nothing to drill into.
 */
export function useDrill(widgetId: string, mode: WidgetMode): ((point?: DrillPoint) => ReturnType<typeof drillHref>) | null {
  const { dashboardId } = useWidgetChrome();
  const kiosk = useIsKiosk();
  if (kiosk || dashboardId === undefined || !canDrill(mode)) return null;
  return (point) => drillHref(dashboardId, widgetId, point);
}
