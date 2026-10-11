// Pure dashboard layout model (no React, no i18n). Mirrors backend/src/tiqora/dashboard/layout.py.
export const ROW_H = 64;
export const GAP = 16;
export const COLS = { lg: 12, md: 8 } as const;
/** Backend limit: y + h may not exceed this ("Das Layout ist zu hoch."). */
export const MAX_ROWS = 1000;
/** Backend limit of widgets per dashboard (`MAX_WIDGETS_PER_DASHBOARD`). */
export const MAX_WIDGETS_PER_DASHBOARD = 40;
/** Backend limit for a single widget height. */
export const MAX_H = 24;

export type Breakpoint = "lg" | "md" | "sm";

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface LayoutItem {
  id: string;
  rect: Rect;
  min: [number, number];
  max: [number, number];
}

export interface EditWidget {
  id: string;
  type_key: string;
  type_version: number;
  title_override: string | null;
  settings: Record<string, unknown>;
  layout: { lg: Rect; md?: Rect };
  /** True: the widget ignores the dashboard-wide filters. */
  pinned?: boolean;
}

/** A stored widget row (API shape) as the edit model; `md` only when stored. */
export function toEditWidget(row: {
  id: string;
  type_key: string;
  type_version: number;
  title_override?: string | null;
  settings?: Record<string, unknown> | null;
  layout: { lg: Rect; md?: Rect | null };
  pinned?: boolean;
}): EditWidget {
  return {
    id: row.id,
    type_key: row.type_key,
    type_version: row.type_version,
    title_override: row.title_override ?? null,
    settings: row.settings ?? {},
    layout: { lg: row.layout.lg, ...(row.layout.md ? { md: row.layout.md } : {}) },
    pinned: row.pinned ?? false,
  };
}
