import type { Dashboard, KioskLink } from "@/lib/dashboardsApi";
import { wallboardRows } from "../wallboard/fit";

export const kioskLinkKeys = {
  all: ["admin", "dashboard-kiosk-links"] as const,
};

/** Number of grid rows a dashboard occupies on the large layout (the TV fit's count; 0 for an empty one). */
export function dashboardRows(d: Pick<Dashboard, "widgets">): number {
  return d.widgets.length === 0 ? 0 : wallboardRows(d.widgets);
}

/** True if a counter or ticket list shows a "mine_*" preset: on a wallboard that is the link creator's queue. */
export function hasMineWidget(d: Pick<Dashboard, "widgets">): boolean {
  return d.widgets.some((w) => {
    if (w.type_key !== "core.counter" && w.type_key !== "core.ticket-list") return false;
    const source = (w.settings as { source?: { kind?: unknown; preset?: unknown } } | null)?.source;
    return source?.kind === "preset" && typeof source.preset === "string" && source.preset.startsWith("mine_");
  });
}

/** The address a screen opens; the secret is the fragment, which no server log sees. */
export function wallboardUrl(token: string): string {
  return `${window.location.origin}/wallboard#${encodeURIComponent(token)}`;
}

/** Active links first (newest first), then the rest by newest. */
export function sortLinks(links: KioskLink[]): KioskLink[] {
  return [...links].sort(
    (a, b) => Number(b.active) - Number(a.active) || b.created_at.localeCompare(a.created_at),
  );
}
