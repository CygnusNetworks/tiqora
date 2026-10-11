import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { useOptionalAuth } from "@/auth/AuthContext";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import type { Dashboard } from "@/lib/dashboardsApi";
import { useDashboardWidgetDefinitions, type WidgetDefinition } from "@/plugins/dashboardWidgets";
import { DashboardGrid, type EditHandlers } from "../DashboardGrid";
import { editDataPlan, type WidgetDataState } from "../data";
import { canUseWidget } from "../definitions";
import { GalleryPanel } from "../gallery/GalleryPanel";
import { GALLERY_OVERLAY_MIN, GALLERY_SIDE_MIN, type GalleryPlacement } from "../gallery/placement";
import { widgetLikeOf, widgetTexts } from "../gallery/galleryModel";
import { MAX_WIDGETS_PER_DASHBOARD, type Breakpoint, type EditWidget } from "../model";
import { toBcp47 } from "@/i18n";
import { formatTimeOfDay } from "@/lib/format";
import { ConflictDialog } from "./ConflictDialog";
import { EditToolbar } from "./EditToolbar";
import { useEditSession } from "./useEditSession";
import { SettingsDrawer } from "../settings/SettingsDrawer";

/** A new widget id matching the server's `^[A-Za-z0-9_-]{8,36}$`. */
function newWidgetId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `w${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;
}

/**
 * Where the gallery sits, by WINDOW width (see `galleryPlacementForWidth`; never by the grid's
 * container, which the open panel itself shrinks). Without `matchMedia` (tests) it is "side".
 */
function useGalleryPlacement(): GalleryPlacement {
  const query = (): GalleryPlacement => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return "side";
    if (window.matchMedia(`(min-width: ${GALLERY_SIDE_MIN}px)`).matches) return "side";
    return window.matchMedia(`(min-width: ${GALLERY_OVERLAY_MIN}px)`).matches ? "overlay" : "none";
  };
  const [placement, setPlacement] = useState(query);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const lists = [window.matchMedia(`(min-width: ${GALLERY_SIDE_MIN}px)`), window.matchMedia(`(min-width: ${GALLERY_OVERLAY_MIN}px)`)];
    const update = () => setPlacement(query());
    lists.forEach((l) => l.addEventListener("change", update));
    return () => lists.forEach((l) => l.removeEventListener("change", update));
  }, []);
  return placement;
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
  galleryHost,
  initialSettingsFor = null,
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
  /** Column next to the grid that holds the gallery on wide screens (inline below the grid when absent). */
  galleryHost?: HTMLElement | null;
  /** Open this widget's settings right away (from a tile's "Suche wählen"), focus in the source section. */
  initialSettingsFor?: string | null;
}) {
  const { t, i18n } = useTranslation();
  const session = useEditSession(dashboard, userId);
  const { state, dispatch } = session;
  const defs = useDashboardWidgetDefinitions();
  const plan = useMemo(() => {
    const hasData = new Set(defs.filter((d) => d.dataEndpoint != null).map((d) => d.key));
    return editDataPlan(savedWidgets, savedData, state.widgets, (key) => hasData.has(key));
  }, [defs, savedWidgets, savedData, state.widgets]);

  const [settingsFor, setSettingsFor] = useState<{ id: string } | null>(
    initialSettingsFor ? { id: initialSettingsFor } : null,
  );
  const [focusSource, setFocusSource] = useState(initialSettingsFor !== null);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const [confirmOverwrite, setConfirmOverwrite] = useState(false);
  const isTemplate = dashboard.kind === "template";
  const [saving, setSaving] = useState(false);
  const gridBoxRef = useRef<HTMLDivElement>(null);
  const toolbarRef = useRef<HTMLDivElement>(null);

  // The widget gallery: open at first on wide screens, closed on tablets (it overlays the grid there).
  const viewportPlacement = useGalleryPlacement();
  // A phone-width grid has no gallery, but an OPEN one is never closed because of the grid's own
  // breakpoint: the open panel shrinks the grid, which would make it flicker open and shut.
  const [galleryChoice, setGalleryChoice] = useState<boolean | null>(null);
  const placement: GalleryPlacement =
    viewportPlacement === "none" || (breakpoint === "sm" && galleryChoice !== true) ? "none" : viewportPlacement;
  const user = useOptionalAuth()?.user;
  const galleryOpen = placement !== "none" && (galleryChoice ?? placement === "side");
  const searchRef = useRef<HTMLInputElement>(null);
  const focusSearch = useRef(false);
  const [added, setAdded] = useState("");
  useEffect(() => {
    if (galleryOpen && focusSearch.current) searchRef.current?.focus();
    focusSearch.current = false;
  }, [galleryOpen]);
  const toggleGallery = () => {
    focusSearch.current = !galleryOpen;
    setGalleryChoice(!galleryOpen);
  };
  const closeGallery = () => {
    setGalleryChoice(false);
    toolbarRef.current?.focus();
  };

  // Entering edit mode puts the focus on the toolbar (announced as "Bearbeiten").
  useEffect(() => {
    if (initialSettingsFor === null) toolbarRef.current?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only on entering
  }, []);

  const settingsWidget = settingsFor ? state.widgets.find((w) => w.id === settingsFor.id) : undefined;
  const settingsDef = settingsWidget ? defs.find((d) => d.key === settingsWidget.type_key) : undefined;
  // The server's 422 for this widget: `settings.<field>` maps onto a field, anything else is general.
  const settingsErrors = useMemo(() => {
    if (!settingsFor || settingsFor.id !== session.invalidWidgetId || !session.invalidMessage) return undefined;
    return { [session.invalidPath ?? ""]: session.invalidMessage };
  }, [settingsFor, session.invalidWidgetId, session.invalidMessage, session.invalidPath]);

  const openSettings = useCallback(
    (id: string) => {
      setFocusSource(false);
      setSettingsFor({ id });
    },
    [],
  );
  // Closing the drawer: the dialog returns the focus to its opener. When that opener is gone (the
  // view-mode "Suche wählen" button unmounted when edit mode started), the widget's cell takes it.
  const refocusAfterSettings = useRef<string | null>(null);
  const closeSettings = useCallback((id: string) => {
    refocusAfterSettings.current = id;
    setSettingsFor(null);
  }, []);
  useEffect(() => {
    const id = refocusAfterSettings.current;
    if (settingsFor !== null || id === null) return;
    refocusAfterSettings.current = null;
    const active = document.activeElement;
    if (active && active !== document.body && active.isConnected) return;
    const cell = Array.from(gridBoxRef.current?.querySelectorAll<HTMLElement>("[data-widget-id]") ?? []).find((el) => el.dataset.widgetId === id);
    (cell ?? toolbarRef.current)?.focus();
  }, [settingsFor]);
  // A 422 naming a widget opens its settings.
  const invalidId = session.invalidWidgetId;
  useEffect(() => {
    if (invalidId) openSettings(invalidId);
  }, [invalidId, openSettings]);

  const focusAfterAdd = useRef<string | null>(null);
  // The newly added widget takes the focus once it is in the grid.
  useEffect(() => {
    const id = focusAfterAdd.current;
    if (!id) return;
    const cell = Array.from(gridBoxRef.current?.querySelectorAll<HTMLElement>("[data-widget-id]") ?? []).find((el) => el.dataset.widgetId === id);
    if (!cell) return;
    focusAfterAdd.current = null;
    cell.focus();
  }, [state.widgets]);

  const bp = breakpoint === "sm" ? null : breakpoint;
  const stateRef = useRef(state);
  stateRef.current = state;
  const addWidget = useCallback(
    (def: WidgetDefinition, at?: { x: number; y: number }) => {
      if (!bp || stateRef.current.widgets.length >= MAX_WIDGETS_PER_DASHBOARD) return;
      const id = newWidgetId();
      dispatch({ type: "add", def: widgetLikeOf(def), id, ...(at ? { at } : {}), bp });
      focusAfterAdd.current = id;
      // The count makes every announcement differ, so adding the same widget twice is read twice.
      setAdded(
        t("dashboards.gallery.addedAt", {
          title: widgetTexts(def, t).title,
          n: stateRef.current.widgets.length + 1,
          max: MAX_WIDGETS_PER_DASHBOARD,
        }),
      );
    },
    [bp, dispatch, t],
  );
  const dropWidget = useCallback(
    (key: string, at: { x: number; y: number }) => {
      const def = defs.find((d) => d.key === key);
      const allowed = { is_admin: user?.is_admin ?? false, can_view_reports: user?.can_view_reports ?? false };
      if (def && canUseWidget(def, allowed)) addWidget(def, at);
    },
    [defs, user?.is_admin, user?.can_view_reports, addWidget],
  );
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
      onDropWidget: dropWidget,
    }),
    [dispatch, openSettings, bp, state.widgets.length, session.invalidWidgetId, plan.previewIds, dropWidget],
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
        gallery={placement === "none" ? undefined : { open: galleryOpen, onToggle: toggleGallery }}
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
      <div aria-live="polite" className="sr-only">
        {added}
      </div>
      {galleryOpen && (placement === "overlay" || !galleryHost) && (
        <GalleryPanel
          ref={searchRef}
          widgetCount={state.widgets.length}
          onAdd={addWidget}
          onClose={closeGallery}
          className={
            placement === "overlay"
              ? "fixed inset-y-3 right-3 z-40 w-[340px] max-w-[calc(100vw-1.5rem)] overflow-y-auto shadow-xl"
              : undefined
          }
        />
      )}
      {galleryOpen &&
        placement === "side" &&
        galleryHost &&
        createPortal(
          <GalleryPanel
            ref={searchRef}
            widgetCount={state.widgets.length}
            onAdd={addWidget}
            onClose={closeGallery}
            className="max-h-[calc(100vh-7rem)] overflow-y-auto"
          />,
          galleryHost,
        )}
      {settingsWidget && settingsDef && (
        <SettingsDrawer
          key={settingsWidget.id}
          widget={settingsWidget}
          def={settingsDef}
          errors={settingsErrors}
          focusSource={focusSource}
          onApply={(settings, titleOverride) => {
            dispatch({ type: "settings", id: settingsWidget.id, settings, titleOverride });
            session.clearInvalid(settingsWidget.id);
            closeSettings(settingsWidget.id);
          }}
          onCancel={() => closeSettings(settingsWidget.id)}
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
