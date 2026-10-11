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
  /**
   * A draft of the same base version from an earlier session, until the user restores or drops it
   * (while it is undecided nothing is autosaved, so the stored draft stays intact).
   */
  restoreAvailable: Draft | null;
  restore: () => void;
  dropDraft: () => void;
  /**
   * The session holds a restored conflict draft (`Draft.overwrites`): saving replaces a version
   * that was saved after these changes were started — the page asks before saving.
   */
  pendingOverwrite: { version: number; at: number } | null;
  /** "Verwerfen": remove the draft and end the session without keeping anything. */
  discard: () => void;
  save: () => Promise<SaveResult>;
  /** Widget named by the last 422 (`widgets[i]…`) and the server's message. */
  invalidWidgetId: string | null;
  invalidMessage: string | null;
  /** Field path inside the widget (`settings.limit`) when the server named one. */
  invalidPath: string | null;
  /** Message for the toolbar after a failed save that names no widget. */
  saveError: string | null;
  /** Server version after a 409 (the conflict dialog is open while set). */
  conflictVersion: number | null;
  dismissConflict: () => void;
  /**
   * After a conflict ("Neu laden"): keep the session as a draft of the newer `version`, marked as
   * overwriting it, so it is offered (with that warning) after the reload. Ends the session.
   */
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
  const [invalid, setInvalid] = useState<{ id: string; message: string | null; path: string | null } | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [conflictVersion, setConflictVersion] = useState<number | null>(null);
  const [overwrite, setOverwrite] = useState<Draft["overwrites"] | null>(null);

  const offeredRef = useRef(offered);
  offeredRef.current = offered;
  const overwriteRef = useRef(overwrite);
  overwriteRef.current = overwrite;
  /** Saved, discarded or handed over to a conflict draft: nothing is flushed on unmount. */
  const finished = useRef(false);
  /** When the session last changed (the draft's `savedAt`). */
  const changedAt = useRef<number | null>(null);
  useEffect(() => {
    if (state.dirty) changedAt.current = Date.now();
  }, [state.dirty, state.widgets, state.name]);

  const draftOf = useCallback(
    (s: EditState, baseVersion: number, overwrites: Draft["overwrites"] | null): Draft => ({
      baseVersion,
      savedAt: changedAt.current ?? Date.now(),
      name: s.name,
      widgets: s.widgets,
      ...(overwrites ? { overwrites } : {}),
    }),
    [],
  );

  // Autosave: 500 ms after the last change; back at the saved state, this session's draft goes.
  // While an earlier draft is offered and undecided, nothing is written (it would overwrite it).
  const wrote = useRef(false);
  const undecided = offered !== null;
  useEffect(() => {
    if (undecided) return;
    if (!state.dirty) {
      if (wrote.current) {
        wrote.current = false;
        clearDraft(userId, dashboardId);
        setDraftSavedAt(null);
      }
      return;
    }
    const timer = setTimeout(() => {
      const draft = draftOf(stateRef.current, stateRef.current.baseVersion, overwriteRef.current);
      if (writeDraft(userId, dashboardId, draft)) {
        wrote.current = true;
        setDraftSavedAt(draft.savedAt);
      }
    }, DRAFT_DELAY_MS);
    return () => clearTimeout(timer);
  }, [undecided, state.dirty, state.widgets, state.name, state.baseVersion, userId, dashboardId, draftOf]);

  // Leaving without saving or discarding (navigation) keeps the last changes, also those of the
  // last 500 ms.
  useEffect(
    () => () => {
      const s = stateRef.current;
      if (finished.current || offeredRef.current || !s.dirty) return;
      writeDraft(userId, dashboardId, draftOf(s, s.baseVersion, overwriteRef.current));
    },
    [userId, dashboardId, draftOf],
  );

  const restore = useCallback(() => {
    if (!offered) return;
    dispatch({ type: "restore", name: offered.name, widgets: offered.widgets });
    setOverwrite(offered.overwrites ?? null);
    setOffered(null);
  }, [offered]);

  const dropDraft = useCallback(() => {
    clearDraft(userId, dashboardId);
    setOffered(null);
  }, [userId, dashboardId]);

  const discard = useCallback(() => {
    finished.current = true;
    clearDraft(userId, dashboardId);
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
      finished.current = true;
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
        if (widget) setInvalid({ id: widget.id, message: bad.message, path: m ? bad.path!.slice(m[0].length).replace(/^\./, "") || null : null });
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
      finished.current = true;
      writeDraft(userId, dashboardId, draftOf(stateRef.current, version, { version, at: Date.now() }));
    },
    [userId, dashboardId, draftOf],
  );

  return {
    state,
    dispatch,
    draftSavedAt,
    restoreAvailable: offered,
    restore,
    dropDraft,
    pendingOverwrite: state.dirty ? (overwrite ?? null) : null,
    discard,
    save,
    invalidWidgetId: invalid?.id ?? null,
    invalidMessage: invalid?.message ?? null,
    invalidPath: invalid?.path ?? null,
    saveError,
    conflictVersion,
    dismissConflict,
    rebaseDraft,
  };
}
