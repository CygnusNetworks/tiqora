import type { WidgetMode } from "@/plugins/dashboardWidgets";

/** View and wallboard are live; edit and preview render the widget inert. */
export function isInteractive(mode: WidgetMode): boolean {
  return mode === "view" || mode === "wallboard";
}

/** Preview and the public wallboard (`kiosk`) render no links at all (plain text instead). */
export function rendersLinks(mode: WidgetMode, kiosk = false): boolean {
  return mode !== "preview" && !kiosk;
}

/** `tabIndex` for a link inside a widget: not a tab stop while editing. */
export function linkTabIndex(mode: WidgetMode): number | undefined {
  return isInteractive(mode) ? undefined : -1;
}

/** `DashboardTicketRow`'s `linkMode` for a widget mode. */
export function rowLinkMode(mode: WidgetMode, kiosk = false): "link" | "inert" | "none" {
  if (!rendersLinks(mode, kiosk)) return "none";
  return isInteractive(mode) ? "link" : "inert";
}

/** Skeleton rows for a widget `h` cells tall (one row = 64 px), so it is sized like the content. */
export function skeletonRows(h: number): number {
  return Math.max(1, Math.min(10, h * 2 - 2));
}
