import type { Dashboard, KioskLink } from "@/lib/dashboardsApi";

/** Rows above which a dashboard is too tall to be readable on a wallboard (matches the TV fit). */
export const WALLBOARD_MAX_ROWS = 14;

export const kioskLinkKeys = {
  all: ["admin", "dashboard-kiosk-links"] as const,
};

/** Number of grid rows a dashboard occupies on the large layout. */
export function dashboardRows(d: Pick<Dashboard, "widgets">): number {
  return d.widgets.reduce((max, w) => Math.max(max, w.layout.lg.y + w.layout.lg.h), 0);
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
