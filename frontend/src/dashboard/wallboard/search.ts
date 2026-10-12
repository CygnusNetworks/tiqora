/** Most dashboards one TV rotation shows. */
export const WALLBOARD_MAX_IDS = 10;
export const ROTATE_MIN = 15;
export const ROTATE_MAX = 900;
export const ROTATE_DEFAULT = 60;

/** Seconds between two full refreshes of the shown dashboard (on top of SSE and widget intervals). */
export const WALLBOARD_REFRESH_SECONDS = 30;
/** The picture is "stale" after this many missed refreshes. */
export const WALLBOARD_STALE_INTERVALS = 3;

export interface WallboardSearch {
  /** Comma list of dashboard ids, e.g. "7,9". */
  ids?: string;
  /** Seconds per dashboard, 15..900. */
  rotate?: number;
}

/** `7`, `"7"` or `"7,9"` -> [7, 9]: positive integers, no duplicates, at most 10. */
export function parseWallboardIds(raw: unknown): number[] {
  const parts = typeof raw === "number" ? [String(raw)] : typeof raw === "string" ? raw.split(",") : [];
  const out: number[] = [];
  for (const part of parts) {
    const text = part.trim();
    if (!/^\d+$/.test(text)) continue;
    const n = Number(text);
    if (Number.isSafeInteger(n) && n > 0 && !out.includes(n)) out.push(n);
    if (out.length >= WALLBOARD_MAX_IDS) break;
  }
  return out;
}

/** Router `validateSearch` of `/agent/wallboard` (the router JSON-parses values, so `7` arrives as a number). */
export function validateWallboardSearch(s: Record<string, unknown>): WallboardSearch {
  const ids = parseWallboardIds(s.ids);
  const rotateRaw = typeof s.rotate === "string" && /^\d+$/.test(s.rotate) ? Number(s.rotate) : s.rotate;
  const rotate =
    typeof rotateRaw === "number" && Number.isInteger(rotateRaw) && rotateRaw >= ROTATE_MIN && rotateRaw <= ROTATE_MAX
      ? rotateRaw
      : undefined;
  return { ...(ids.length > 0 ? { ids: ids.join(",") } : {}), ...(rotate !== undefined ? { rotate } : {}) };
}
