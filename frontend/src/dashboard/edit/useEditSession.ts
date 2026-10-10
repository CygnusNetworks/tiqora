/**
 * One edit session of a dashboard: the undo/redo reducer (Task 8) with the registered widget
 * sizes, the autosaved draft, and saving with the optimistic lock. Mounted only while editing.
 */
import { useCallback, useMemo, useReducer, useRef, useState, useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/lib/api";
import { DashboardConflictError, dashboardKeys, dashboardsApi, type Dashboard } from "@/lib/dashboardsApi";
import { useDashboardWidgetDefinitions } from "@/plugins/dashboardWidgets";
import { layoutTooTall } from "../layout/breakpoints";
import { createEditReducer, initialEditState, type EditAction, type EditState, type SizeLookup } from "../layout/session";
import { toEditWidget } from "../model";
import { clearDraft, readDraft, writeDraft, type Draft } from "./draft";

/** The draft is written this long after the last change. */
export const DRAFT_DELAY_MS = 500;

export type SaveResult = "saved" | "conflict" | "invalid" | "error";

export interface EditSession {
  state: EditState;
  dispatch: (a: EditAction) => void;
  /** When the last draft write succeeded (null: nothing written, or storage blocked). */
  draftSavedAt: number | null;
  /** A draft of the same base version from an earlier session (until the first change). */
  restoreAvailable: Draft | null;
  restore: () => void;
  dropDraft: () => void;
  save: () => Promise<SaveResult>;
  /** Widget named by the last 422 (`widgets[i]…`) and the server's message. */
  invalidWidgetId: string | null;
  invalidMessage: string | null;
  /** Message for the toolbar after a failed save that names no widget. */
  saveError: string | null;
  /** Server version after a 409 (the conflict dialog is open while set). */
  conflictVersion: number | null;
  dismissConflict: () => void;
  /** After a conflict: keep the session as a draft of `version`, so it is offered after the reload. */
  rebaseDraft: (version: number) => void;
}

const WIDGET_PATH = /^widgets\[(\d+)\]/;

/** `{message, path}` of a 422, wrapped in `detail` (HTTPException) or bare. */
function invalidFrom(err: unknown): { message: string | null; path: string | null } | null {
  if (!(err instanceof ApiError) || err.status !== 422) return null;
  const body = err.detail as Record<string, unknown> | null;
  const inner = body && typeof body === "object" && "detail" in body ? body.detail : body;
  if (Array.isArray(inner)) {
    const first = inner[0] as { msg?: unknown } | undefined;
    return { message: typeof first?.msg === "string" ? first.msg : null, path: null };
  }
  if (!inner || typeof inner !== "object") return { message: typeof inner === "string" ? inner : null, path: null };
  const { message, path } = inner as { message?: unknown; path?: unknown };
  return { message: typeof message === "string" ? message : null, path: typeof path === "string" ? path : null };
}

export function useEditSession(dashboard: Dashboard, userId: number): EditSession {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const dashboardId = dashboard.id;

  // Size limits of the registered types, read through a ref so the reducer stays stable.
  const defs = useDashboardWidgetDefinitions();
  const sizesRef = useRef(new Map<string, { min: [number, number]; max: [number, number] }>());
  sizesRef.current = new Map(defs.map((d) => [d.key, { min: d.size.min, max: d.size.max }]));
  const reducer = useMemo(() => {
    const sizes: SizeLookup = (key) => sizesRef.current.get(key);
    return createEditReducer(sizes);
  }, []);
  const [state, dispatch] = useReducer(reducer, dashboard, (d) =>
    initialEditState(d.name, d.widgets.map(toEditWidget), d.version),
  );
  const stateRef = useRef(state);
  stateRef.current = state;

  const [offered, setOffered] = useState<Draft | null>(() => readDraft(userId, dashboardId, dashboard.version));
  const [draftSavedAt, setDraftSavedAt] = useState<number | null>(null);
  const [invalid, setInvalid] = useState<{ id: string; message: string | null } | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [conflictVersion, setConflictVersion] = useState<number | null>(null);

  // Autosave: 500 ms after the last change; back at the saved state, this session's draft goes.
  const wrote = useRef(false);
  useEffect(() => {
    if (!state.dirty) {
      if (wrote.current) {
        wrote.current = false;
        clearDraft(userId, dashboardId);
        setDraftSavedAt(null);
      }
      return;
    }
    const timer = setTimeout(() => {
      const savedAt = Date.now();
      const draft: Draft = { baseVersion: state.baseVersion, savedAt, name: state.name, widgets: state.widgets };
      if (writeDraft(userId, dashboardId, draft)) {
        wrote.current = true;
        setDraftSavedAt(savedAt);
        setOffered(null); // the earlier draft is overwritten now
      }
    }, DRAFT_DELAY_MS);
    return () => clearTimeout(timer);
  }, [state.dirty, state.widgets, state.name, state.baseVersion, userId, dashboardId]);

  const restore = useCallback(() => {
    if (!offered) return;
    dispatch({ type: "restore", name: offered.name, widgets: offered.widgets });
    setOffered(null);
  }, [offered]);

  const dropDraft = useCallback(() => {
    clearDraft(userId, dashboardId);
    setOffered(null);
  }, [userId, dashboardId]);

  const icon = dashboard.icon ?? null;
  const save = useCallback(async (): Promise<SaveResult> => {
    const s = stateRef.current;
    setSaveError(null);
    setInvalid(null);
    // The server would answer 422 "Das Layout ist zu hoch." (normalizeRects at 12 and 8 columns).
    if (layoutTooTall(s.widgets)) {
      setSaveError(t("dashboards.edit.tooTall"));
      return "invalid";
    }
    try {
      const saved = await dashboardsApi.save(dashboardId, {
        name: s.name,
        icon,
        version: s.baseVersion,
        widgets: s.widgets.map((w) => ({ ...w })),
      });
      clearDraft(userId, dashboardId);
      wrote.current = false;
      qc.setQueryData(dashboardKeys.one(dashboardId), saved);
      void qc.invalidateQueries({ queryKey: dashboardKeys.all });
      return "saved";
    } catch (err) {
      if (err instanceof DashboardConflictError) {
        setConflictVersion(err.currentVersion);
        return "conflict";
      }
      const bad = invalidFrom(err);
      if (bad) {
        const m = bad.path ? WIDGET_PATH.exec(bad.path) : null;
        const widget = m ? s.widgets[Number(m[1])] : undefined;
        if (widget) setInvalid({ id: widget.id, message: bad.message });
        else setSaveError(bad.message ?? t("dashboards.edit.saveFailed"));
        return "invalid";
      }
      setSaveError(t("dashboards.edit.saveFailed"));
      return "error";
    }
  }, [dashboardId, icon, userId, qc, t]);

  const dismissConflict = useCallback(() => setConflictVersion(null), []);
  const rebaseDraft = useCallback(
    (version: number) => {
      const s = stateRef.current;
      writeDraft(userId, dashboardId, { baseVersion: version, savedAt: Date.now(), name: s.name, widgets: s.widgets });
    },
    [userId, dashboardId],
  );

  return {
    state,
    dispatch,
    draftSavedAt,
    // superseded by the first change (the autosave overwrites it)
    restoreAvailable: state.dirty ? null : offered,
    restore,
    dropDraft,
    save,
    invalidWidgetId: invalid?.id ?? null,
    invalidMessage: invalid?.message ?? null,
    saveError,
    conflictVersion,
    dismissConflict,
    rebaseDraft,
  };
}
