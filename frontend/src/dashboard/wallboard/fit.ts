/** Gap between wallboard tiles (px at the design size). */
export const WALLBOARD_GAP = 24;
/** Above this many rows the link dialog warns "zu hoch für ein Wallboard". */
export const WALLBOARD_MAX_ROWS = 14;
const MIN_ROW = 48;
const MAX_ROW = 160;
/** Design size of the approved artboard. */
const DESIGN_WIDTH = 1920;

function scaled(base: number, width: number): number {
  return Math.round((base * Math.max(1, width)) / DESIGN_WIDTH);
}

/** Page padding: 40 / 48 px at 1920 wide, scaled with the viewport width. */
export function wallboardPadding(viewport: { width: number }): { x: number; y: number } {
  return { x: scaled(48, viewport.width), y: scaled(40, viewport.width) };
}

/** Height of the header block (name row and its gap to the grid). */
export function wallboardHeaderPx(viewport: { width: number }): number {
  return scaled(96, viewport.width);
}

/**
 * Row height at which `rows` rows plus the header fit into the viewport without scrolling,
 * clamped to 48..160 px (the 24 px gaps are part of the fit).
 */
export function wallboardRowHeight(viewport: { width: number; height: number }, rows: number, headerPx: number): number {
  const n = Math.max(1, Math.floor(rows));
  const pad = wallboardPadding(viewport).y * 2;
  const room = viewport.height - pad - headerPx - (n - 1) * WALLBOARD_GAP;
  return Math.min(MAX_ROW, Math.max(MIN_ROW, Math.floor(room / n)));
}

/** Rows the board needs: `max(y + h)` over the `lg` rects (at least 1). */
export function wallboardRows(widgets: readonly { layout: { lg: { y: number; h: number } } }[]): number {
  let rows = 1;
  for (const w of widgets) rows = Math.max(rows, w.layout.lg.y + w.layout.lg.h);
  return rows;
}
