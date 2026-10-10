import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import type { Dashboard } from "@/lib/dashboardsApi";
import { useDashboardWidgetDefinitions } from "@/plugins/dashboardWidgets";
import { DashboardGrid, type EditHandlers } from "../DashboardGrid";
import { editDataPlan, type WidgetDataState } from "../data";
import { MAX_WIDGETS_PER_DASHBOARD, type Breakpoint, type EditWidget } from "../model";
import { toBcp47 } from "@/i18n";
import { formatTimeOfDay } from "@/lib/format";
import { ConflictDialog } from "./ConflictDialog";
import { EditToolbar } from "./EditToolbar";
import { useEditSession } from "./useEditSession";
import { WidgetSettingsDialog } from "./WidgetSettingsDialog";

/** A new widget id matching the server's `^[A-Za-z0-9_-]{8,36}$`. */
function newWidgetId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `w${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;
}

const EDITABLE_TARGET = "input, textarea, select, [contenteditable=''], [contenteditable='true'], [role='dialog'], [role='alertdialog']";

/**
 * Edit mode of one dashboard, mounted only while editing (leaving unmounts the session): the dark
 * toolbar, the grid with handles, drag, resize and keyboard, the settings seam, "Verwerfen" (asks
 * when something changed) and "Speichern" with the conflict dialog.
 */
export function DashboardEditMode({
  dashboard,
  userId,
  breakpoint,
  savedWidgets,
  savedData,
  onLeave,
  onReload,
}: {
  dashboard: Dashboard;
  userId: number;
  breakpoint: Breakpoint;
  /** The dashboard as loaded, and the data the view had for it (reused while unchanged). */
  savedWidgets: EditWidget[];
  savedData: Record<string, WidgetDataState>;
  /** Back to view mode (after "Speichern", or "Verwerfen"). */
  onLeave: () => void;
  /** After a conflict: leave and load the dashboard again. */
  onReload: () => void;
}) {
  const { t, i18n } = useTranslation();
  const session = useEditSession(dashboard, userId);
  const { state, dispatch } = session;
  const defs = useDashboardWidgetDefinitions();
  const plan = useMemo(() => {
    const hasData = new Set(defs.filter((d) => d.dataEndpoint != null).map((d) => d.key));
    return editDataPlan(savedWidgets, savedData, state.widgets, (key) => hasData.has(key));
  }, [defs, savedWidgets, savedData, state.widgets]);

  const [settingsFor, setSettingsFor] = useState<{ id: string; title: string } | null>(null);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const [confirmOverwrite, setConfirmOverwrite] = useState(false);
  const isTemplate = dashboard.kind === "template";
  const [saving, setSaving] = useState(false);
  const gridBoxRef = useRef<HTMLDivElement>(null);
  const toolbarRef = useRef<HTMLDivElement>(null);

  // Entering edit mode puts the focus on the toolbar (announced as "Bearbeiten").
  useEffect(() => {
    toolbarRef.current?.focus();
  }, []);

  const openSettings = useCallback(
    (id: string) => {
      const cell = Array.from(gridBoxRef.current?.querySelectorAll<HTMLElement>("[data-widget-id]") ?? []).find(
        (el) => el.dataset.widgetId === id,
      );
      setSettingsFor({ id, title: cell?.getAttribute("aria-label") ?? "" });
    },
    [],
  );
  // A 422 naming a widget opens its settings.
  const invalidId = session.invalidWidgetId;
  useEffect(() => {
    if (invalidId) openSettings(invalidId);
  }, [invalidId, openSettings]);

  const bp = breakpoint === "sm" ? null : breakpoint;
  const handlers = useMemo<EditHandlers>(
    () => ({
      onRemove: (id) => dispatch({ type: "remove", id }),
      onDuplicate: (id) => dispatch({ type: "duplicate", id, newId: newWidgetId() }),
      onOpenSettings: openSettings,
      onMove: (id, to, via) => {
        if (bp) dispatch({ type: "move", id, to, bp, ...(via === "keyboard" ? { coalesce: true, now: Date.now() } : {}) });
      },
      onResize: (id, size, via) => {
        if (bp) dispatch({ type: "resize", id, size, bp, ...(via === "keyboard" ? { coalesce: true, now: Date.now() } : {}) });
      },
      onShift: (id, dir) => dispatch({ type: "shift", id, dir }),
      canDuplicate: state.widgets.length < MAX_WIDGETS_PER_DASHBOARD,
      invalidWidgetId: session.invalidWidgetId,
      previewIds: plan.previewIds,
    }),
    [dispatch, openSettings, bp, state.widgets.length, session.invalidWidgetId, plan.previewIds],
  );

  // Ctrl/Cmd+Z undoes, Shift+Ctrl/Cmd+Z and Ctrl/Cmd+Y redo — not inside fields or dialogs.
  const dialogOpen = settingsFor !== null || confirmDiscard || confirmOverwrite || session.conflictVersion !== null;
  const dialogOpenRef = useRef(dialogOpen);
  dialogOpenRef.current = dialogOpen;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
      const key = e.key.toLowerCase();
      if (key !== "z" && key !== "y") return;
      if (dialogOpenRef.current || (e.target as Element | null)?.closest?.(EDITABLE_TARGET)) return;
      e.preventDefault();
      dispatch({ type: key === "y" || e.shiftKey ? "redo" : "undo" });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [dispatch]);

  const save = async (confirmed = false) => {
    // A restored conflict draft replaces a newer version: say so before sending it.
    if (session.pendingOverwrite && !confirmed) {
      setConfirmOverwrite(true);
      return;
    }
    setConfirmOverwrite(false);
    setSaving(true);
    const result = await session.save();
    if (result === "saved") onLeave();
    else setSaving(false);
  };

  return (
    <div className="space-y-5">
      <EditToolbar
        ref={toolbarRef}
        name={state.name}
        draftSavedAt={session.draftSavedAt}
        restoreAt={session.restoreAvailable?.savedAt ?? null}
        restoreReplacesAt={session.restoreAvailable?.overwrites?.at ?? null}
        onRestore={session.restore}
        onDropDraft={session.dropDraft}
        canUndo={state.past.length > 0}
        canRedo={state.future.length > 0}
        onUndo={() => dispatch({ type: "undo" })}
        onRedo={() => dispatch({ type: "redo" })}
        onDiscard={() => (state.dirty ? setConfirmDiscard(true) : onLeave())}
        onSave={() => void save()}
        saving={saving}
        error={session.saveError}
      />
      <div ref={gridBoxRef}>
        <DashboardGrid
          widgets={state.widgets}
          mode="edit"
          breakpoint={breakpoint}
          dashboardId={dashboard.id}
          canEdit
          data={plan.byWidget}
          onRetry={() => {}}
          edit={handlers}
        />
      </div>
      {settingsFor && (
        <WidgetSettingsDialog
          title={settingsFor.title}
          message={settingsFor.id === session.invalidWidgetId ? session.invalidMessage : null}
          onClose={() => setSettingsFor(null)}
        />
      )}
      <ConfirmDialog
        open={confirmDiscard}
        role="alertdialog"
        variant="danger"
        title={t("dashboards.edit.discardTitle")}
        message={t("dashboards.edit.discardMessage")}
        confirmLabel={t("dashboards.edit.discardConfirm")}
        cancelLabel={t("dashboards.edit.continue")}
        onCancel={() => setConfirmDiscard(false)}
        onConfirm={() => {
          session.discard();
          onLeave();
        }}
      />
      <ConfirmDialog
        open={confirmOverwrite}
        role="alertdialog"
        variant="danger"
        title={t("dashboards.edit.overwriteTitle")}
        message={t(isTemplate ? "dashboards.edit.overwriteMessageTemplate" : "dashboards.edit.overwriteMessage", {
          newer: session.pendingOverwrite ? formatTimeOfDay(new Date(session.pendingOverwrite.at), toBcp47(i18n.language)) : "",
        })}
        confirmLabel={t("dashboards.edit.overwriteConfirm")}
        cancelLabel={t("dashboards.edit.continue")}
        onCancel={() => setConfirmOverwrite(false)}
        onConfirm={() => void save(true)}
      />
      <ConflictDialog
        open={session.conflictVersion !== null}
        template={isTemplate}
        onContinue={session.dismissConflict}
        onReload={() => {
          if (session.conflictVersion !== null) session.rebaseDraft(session.conflictVersion);
          onReload();
        }}
      />
    </div>
  );
}
