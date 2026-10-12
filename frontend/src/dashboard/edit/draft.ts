/**
 * The autosaved edit draft of one dashboard (per browser, per user). A convenience only: every
 * storage access is guarded, blocked or full storage simply means "no draft". A draft belongs to
 * the dashboard version it was started from (`baseVersion`); one of an older version is dropped.
 * A draft of a *newer* version than the one loaded is kept (not offered): after a conflict the
 * page may still show the old version for a moment while the reload runs.
 */
import type { EditWidget } from "../model";

export interface Draft {
  baseVersion: number;
  /** When the changes were last made. */
  savedAt: number;
  name: string;
  widgets: EditWidget[];
  /**
   * Set when the draft was kept after a save conflict ("Neu laden"): it was started from an older
   * version, so restoring and saving it replaces the newer version `version` (seen at `at`).
   */
  overwrites?: { version: number; at: number };
}

export const draftKey = (userId: number, dashboardId: number) => `tiqora.dashboard.draft.${userId}.${dashboardId}`;

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const isInt = (v: unknown): v is number => Number.isInteger(v);
const isRect = (v: unknown) => isObject(v) && isInt(v.x) && isInt(v.y) && isInt(v.w) && isInt(v.h);

function isWidget(v: unknown): v is EditWidget {
  return (
    isObject(v) &&
    typeof v.id === "string" &&
    typeof v.type_key === "string" &&
    isInt(v.type_version) &&
    (v.title_override === null || typeof v.title_override === "string") &&
    isObject(v.settings) &&
    isObject(v.layout) &&
    isRect(v.layout.lg) &&
    (v.layout.md === undefined || isRect(v.layout.md))
  );
}

function isOverwrite(v: unknown): boolean {
  return v === undefined || (isObject(v) && isInt(v.version) && typeof v.at === "number" && Number.isFinite(v.at));
}

function isDraft(v: unknown): v is Draft {
  return (
    isObject(v) &&
    isOverwrite(v.overwrites) &&
    isInt(v.baseVersion) &&
    typeof v.savedAt === "number" &&
    Number.isFinite(v.savedAt) &&
    typeof v.name === "string" &&
    Array.isArray(v.widgets) &&
    v.widgets.every(isWidget)
  );
}

/**
 * The draft started from `baseVersion`. An older version or a malformed entry is removed (null);
 * one of a newer version stays stored but is not offered (null).
 */
export function readDraft(userId: number, dashboardId: number, baseVersion: number): Draft | null {
  let raw: string | null;
  try {
    raw = window.localStorage.getItem(draftKey(userId, dashboardId));
  } catch {
    return null;
  }
  if (raw === null) return null;
  let parsed: unknown = null;
  try {
    parsed = JSON.parse(raw);
  } catch {
    parsed = null;
  }
  if (isDraft(parsed) && parsed.baseVersion === baseVersion) {
    // Drafts written before `pinned` existed read as "filters apply".
    return { ...parsed, widgets: parsed.widgets.map((w) => ({ ...w, pinned: w.pinned === true })) };
  }
  if (isDraft(parsed) && parsed.baseVersion > baseVersion) return null;
  clearDraft(userId, dashboardId);
  return null;
}

/** False when storage is blocked or full. */
export function writeDraft(userId: number, dashboardId: number, draft: Draft): boolean {
  try {
    window.localStorage.setItem(draftKey(userId, dashboardId), JSON.stringify(draft));
    return true;
  } catch {
    return false;
  }
}

export function clearDraft(userId: number, dashboardId: number): void {
  try {
    window.localStorage.removeItem(draftKey(userId, dashboardId));
  } catch {
    // blocked storage: nothing stored either
  }
}
