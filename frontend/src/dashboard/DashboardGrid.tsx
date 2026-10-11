import {
  Component,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type DragEvent,
  type ErrorInfo,
  type HTMLAttributes,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";
import { useTranslation } from "react-i18next";
import { useOptionalAuth } from "@/auth/AuthContext";
import { cn } from "@/lib/cn";
import { usePlugins } from "@/plugins/PluginProvider";
import {
  useDashboardWidgetDefinitions,
  type WidgetDefinition,
  type WidgetMode,
} from "@/plugins/dashboardWidgets";
import { WidgetChromeProvider, WidgetFrame, type WidgetChrome } from "./chrome/WidgetFrame";
import { WidgetForbidden } from "./chrome/WidgetError";
import { WidgetSkeleton } from "./chrome/WidgetSkeleton";
import { WidgetUnavailable } from "./chrome/WidgetUnavailable";
import { skeletonRows } from "./chrome/mode";
import { usePreviewData, WidgetDataProvider, type WidgetDataState } from "./data";
import { canUseWidget, settingsWithDefaults } from "./definitions";
import { GearIcon, PhoneControls } from "./edit/PhoneControls";
import { useDragResize, type DragCommit } from "./edit/useDragResize";
import { WIDGET_DRAG_TYPE } from "./gallery/galleryModel";
import { layoutForBreakpoint } from "./layout/breakpoints";
import { pixelsToCell } from "./layout/grid";
import { keyToAction } from "./layout/keyboard";
import { layoutItems, type SizeLookup } from "./layout/session";
import { COLS, GAP, MAX_WIDGETS_PER_DASHBOARD, ROW_H, type Breakpoint, type EditWidget, type LayoutItem, type Rect } from "./model";

/** Edit-mode callbacks of the grid; every action is optional (a missing handler hides its button). */
export interface EditHandlers {
  /** "Entfernen" on a widget (also on the tile of an unavailable widget type). */
  onRemove?: (id: string) => void;
  onDuplicate?: (id: string) => void;
  /** "Einstellungen", Enter on a focused widget. */
  onOpenSettings?: (id: string) => void;
  /** Drag (one call on drop) or arrow keys, in cells of the current breakpoint. */
  onMove?: (id: string, to: { x: number; y: number }, via: "pointer" | "keyboard") => void;
  onResize?: (id: string, size: { w: number; h: number }, via: "pointer" | "keyboard") => void;
  /** Phone order (array order). */
  onShift?: (id: string, dir: "up" | "down") => void;
  /** False at the widget limit (duplicating is disabled). */
  canDuplicate?: boolean;
  /** Widget named by the last failed save: danger outline. */
  invalidWidgetId?: string | null;
  /** Data widgets whose data comes from the preview route (new or changed settings). */
  previewIds?: ReadonlySet<string>;
  /** A gallery card dropped on the grid: the widget key and the snapped cell under the pointer. */
  onDropWidget?: (key: string, at: { x: number; y: number }) => void;
}

const NO_FILTERS: Record<string, unknown> = {};
const NONE_STATE: WidgetDataState = { status: "none" };
const COUNTER = "core.counter";
const NO_ITEMS: LayoutItem[] = [];

type CellBoundaryProps = { fallback: ReactNode; resetKeys: readonly unknown[]; children: ReactNode };

/**
 * Catches a widget that throws while rendering, so one broken widget never takes the page down.
 * New data or settings (`resetKeys`) give the widget another try.
 */
class CellBoundary extends Component<CellBoundaryProps, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  componentDidUpdate(prev: CellBoundaryProps): void {
    const changed =
      prev.resetKeys.length !== this.props.resetKeys.length ||
      prev.resetKeys.some((key, i) => !Object.is(key, this.props.resetKeys[i]));
    if (this.state.failed && changed) this.setState({ failed: false });
  }

  componentDidCatch(error: unknown, _info: ErrorInfo): void {
    console.error("[dashboard] widget failed", error);
  }

  render(): ReactNode {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

function PlainTile({ children }: { children: ReactNode }) {
  return (
    <article className="flex h-full min-h-0 min-w-0 flex-col gap-2 rounded-[14px] border border-hairline bg-surface px-[18px] py-4">
      {children}
    </article>
  );
}

/** Edit-mode extras of one cell: header chrome, wrapper attributes and what follows the tile. */
interface CellEdit {
  chrome: Pick<WidgetChrome, "handle" | "actions">;
  attrs: HTMLAttributes<HTMLDivElement>;
  className?: string;
  after?: ReactNode;
}

function GridCell({
  widget,
  def,
  state,
  rect,
  mode,
  canEdit,
  onRetry,
  retry,
  edit,
  onConfigure,
  cellEdit,
  style,
}: {
  widget: EditWidget;
  def: WidgetDefinition | undefined;
  state: WidgetDataState;
  rect: Rect;
  mode: WidgetMode;
  canEdit: boolean;
  onRetry: (id: string) => void;
  /** Overrides `onRetry(id)` (preview data). */
  retry?: () => void;
  edit?: EditHandlers;
  /** View mode: the in-tile "choose source" actions (only handed over for dashboards the viewer can edit). */
  onConfigure?: (id: string) => void;
  cellEdit?: CellEdit;
  style?: CSSProperties;
}) {
  const { t } = useTranslation();
  const { ready: pluginsReady } = usePlugins();
  const user = useOptionalAuth()?.user;
  const settings = useMemo(
    () => (def ? settingsWithDefaults(def, widget.settings, widget.type_version) : {}),
    [def, widget.settings, widget.type_version],
  );
  const id = widget.id;
  // Stable per cell, so the widget does not re-render with every grid render.
  const dataValue = useMemo(
    () => ({ ...state, refetch: retry ?? (() => onRetry(id)) }),
    [state, retry, onRetry, id],
  );
  const handle = cellEdit?.chrome.handle;
  const actions = cellEdit?.chrome.actions;
  const chrome = useMemo(
    () => ({ titleOverride: widget.title_override, canEdit, handle, actions, onConfigure: edit?.onOpenSettings ? () => edit.onOpenSettings?.(id) : onConfigure && (() => onConfigure(id)) }),
    [widget.title_override, canEdit, handle, actions, edit, onConfigure, id],
  );
  const waiting = state.status === "loading" || state.status === "pending";
  const size = { w: rect.w, h: rect.h };
  const defTitle = def ? t(def.titleKey, { ns: def.ns, defaultValue: def.key }) : t("dashboards.state.unavailable");

  let body: ReactNode;
  if (!def) {
    // Plugin types register once the plugin list arrived; until then the slot only waits.
    body =
      !pluginsReady && !widget.type_key.startsWith("core.") ? (
        <PlainTile>
          <WidgetSkeleton rows={skeletonRows(rect.h)} />
        </PlainTile>
      ) : (
        <WidgetChromeProvider value={chrome}>
          <WidgetUnavailable
            typeKey={widget.type_key}
            mode={mode}
            onRemove={edit?.onRemove ? () => edit.onRemove?.(id) : undefined}
          />
        </WidgetChromeProvider>
      );
  } else if (!canUseWidget(def, { is_admin: user?.is_admin === true, can_view_reports: user?.can_view_reports === true })) {
    body = (
      <WidgetChromeProvider value={chrome}>
        <WidgetFrame title={widget.title_override?.trim() || defTitle} mode={mode} testId="widget-forbidden">
          <WidgetForbidden message={t("dashboards.state.forbidden")} />
        </WidgetFrame>
      </WidgetChromeProvider>
    );
  } else {
    const Widget = def.component;
    const content = (
      <Widget instanceId={id} settings={settings} size={size} filters={NO_FILTERS} mode={mode} />
    );
    body = (
      <WidgetChromeProvider value={chrome}>
        <WidgetDataProvider value={dataValue}>
          {def.owner === "core" ? (
            content
          ) : (
            // Core widgets draw their own frame; plugin widgets get the host's (PluginBoundary is inside the component).
            <WidgetFrame title={defTitle} mode={mode} testId={`plugin-widget-${def.key}`}>
              {content}
            </WidgetFrame>
          )}
        </WidgetDataProvider>
      </WidgetChromeProvider>
    );
  }

  // Edit mode: the cell is named after the title the widget shows (presets, overrides).
  const cellRef = useRef<HTMLDivElement>(null);
  const [shownTitle, setShownTitle] = useState(defTitle);
  const editingCell = cellEdit !== undefined;
  useLayoutEffect(() => {
    if (!editingCell) return;
    const text = cellRef.current?.querySelector("h2")?.textContent?.trim() || defTitle;
    if (text !== shownTitle) setShownTitle(text);
  }, [editingCell, defTitle, shownTitle, state, widget, settings]);

  return (
    <div
      ref={cellRef}
      data-testid={waiting ? `widget-${id}-skeleton` : `widget-${id}`}
      data-widget-id={id}
      data-type={widget.type_key}
      className={cn("min-h-0 min-w-0", cellEdit?.className)}
      style={style}
      {...(cellEdit ? { ...cellEdit.attrs, "aria-label": shownTitle } : {})}
    >
      <CellBoundary
        resetKeys={[state.data, state.status, widget.settings, widget.type_key, widget.type_version]}
        fallback={
          <PlainTile>
            <p role="alert" className="text-sm text-danger">
              {t("dashboards.view.widgetCrashed")}
            </p>
          </PlainTile>
        }
      >
        {body}
      </CellBoundary>
      {cellEdit?.after}
    </div>
  );
}

/** A changed or new data widget while editing: its data comes from the preview route. */
function PreviewGridCell(props: Omit<Parameters<typeof GridCell>[0], "state" | "retry">) {
  const { refetch, ...state } = usePreviewData(props.widget.type_key, props.widget.settings);
  return <GridCell {...props} state={state} retry={refetch} />;
}

function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(() => {
    try {
      return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
    } catch {
      return false;
    }
  });
  useEffect(() => {
    let query: MediaQueryList | undefined;
    try {
      query = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    } catch {
      query = undefined;
    }
    if (!query?.addEventListener) return;
    const onChange = () => setReduced(query!.matches);
    query.addEventListener("change", onChange);
    return () => query!.removeEventListener("change", onChange);
  }, []);
  return reduced;
}

const ICON_BTN =
  "inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-[7px] border border-hairline bg-surface text-muted transition-colors hover:bg-surface-subtle hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-40";

function DuplicateIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
      <rect x="5" y="5" width="9" height="9" rx="2" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path d="M11 5V3.5A1.5 1.5 0 0 0 9.5 2h-6A1.5 1.5 0 0 0 2 3.5v6A1.5 1.5 0 0 0 3.5 11H5" fill="none" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  );
}

function RemoveIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
      <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

const GUIDE_TINT = "color-mix(in srgb, var(--color-accent) 7%, transparent)";

/**
 * The widgets of one dashboard. `lg`/`md`: CSS grid of 12/8 columns, 64 px rows, 16 px gap, each
 * widget placed by `grid-column`/`grid-row` from its rect (clamped and overlap-free; RTL mirrors via
 * the grid's direction). `sm`: one column in array order, consecutive counters two per row.
 *
 * Edit mode (`mode="edit"` with `edit`): column guide, a grab handle and Einstellungen / Duplizieren
 * / Entfernen per widget, a resize corner, pointer drag with a live preview and drop target,
 * keyboard moving/resizing with a live announcement. On a phone only ↑/↓ and the settings remain.
 */
export function DashboardGrid({
  widgets,
  mode,
  breakpoint,
  dashboardId,
  canEdit,
  data,
  onRetry,
  edit,
  onConfigureWidget,
}: {
  widgets: EditWidget[];
  mode: "view" | "edit";
  breakpoint: Breakpoint;
  dashboardId: number;
  canEdit: boolean;
  data: Record<string, WidgetDataState>;
  onRetry: (id: string) => void;
  edit?: EditHandlers;
  /** View mode, editors only: a tile's "Suche wählen" / "Filter ändern" opens that widget's settings in edit mode. */
  onConfigureWidget?: (id: string) => void;
}) {
  const { t } = useTranslation();
  const defs = useDashboardWidgetDefinitions();
  const defByKey = useMemo(() => new Map(defs.map((d) => [d.key, d])), [defs]);
  const editing = mode === "edit" && edit !== undefined;
  const wide = breakpoint !== "sm";
  const cols = wide ? COLS[breakpoint] : COLS.lg;

  const sizes = useCallback<SizeLookup>(
    (key) => {
      const d = defByKey.get(key);
      return d ? { min: d.size.min, max: d.size.max } : undefined;
    },
    [defByKey],
  );
  // While editing, the same items the reducer works on (so the drag preview equals the result).
  const items = useMemo(
    () => (editing && wide ? layoutItems(widgets, breakpoint, sizes) : NO_ITEMS),
    [editing, wide, widgets, breakpoint, sizes],
  );
  const rects = useMemo(() => {
    if (!wide) return null;
    if (editing) return new Map(items.map((i) => [i.id, i.rect]));
    return layoutForBreakpoint(widgets, breakpoint, (key) => defByKey.get(key)?.size.min ?? [1, 1]);
  }, [wide, editing, items, widgets, breakpoint, defByKey]);

  const gridRef = useRef<HTMLDivElement>(null);
  const reducedMotion = usePrefersReducedMotion();

  // ---- announcements (aria-live) and focus after an action
  const [pending, setPending] = useState<{ id: string; kind: "rect" | "order"; n: number } | null>(null);
  const [message, setMessage] = useState("");
  const focusNext = useRef<{ id: string; prefer?: string } | null>(null);
  const cellOf = useCallback(
    (id: string) =>
      Array.from(gridRef.current?.querySelectorAll<HTMLElement>("[data-widget-id]") ?? []).find(
        (el) => el.dataset.widgetId === id,
      ),
    [],
  );
  const titleOf = useCallback(
    (id: string) => {
      const cell = cellOf(id);
      return cell?.getAttribute("aria-label") || cell?.querySelector("h2")?.textContent?.trim() || "";
    },
    [cellOf],
  );
  const announce = useCallback((id: string, kind: "rect" | "order") => {
    setPending((p) => ({ id, kind, n: (p?.n ?? 0) + 1 }));
  }, []);
  useEffect(() => {
    if (!pending) return;
    const title = titleOf(pending.id);
    if (pending.kind === "rect") {
      const r = rects?.get(pending.id);
      if (r) setMessage(t("dashboards.edit.moved", { title, col: r.x + 1, row: r.y + 1, w: r.w, h: r.h }));
    } else {
      const pos = widgets.findIndex((w) => w.id === pending.id);
      if (pos >= 0) setMessage(t("dashboards.edit.movedOrder", { title, pos: pos + 1, count: widgets.length }));
    }
  }, [pending, rects, widgets, titleOf, t]);
  useEffect(() => {
    const target = focusNext.current;
    if (!target) return;
    focusNext.current = null;
    const cell = cellOf(target.id);
    const preferred = target.prefer ? cell?.querySelector<HTMLElement>(target.prefer) : null;
    const button = preferred && !preferred.hasAttribute("disabled") ? preferred : null;
    (button ?? cell ?? gridRef.current)?.focus();
  }, [widgets, cellOf]);

  const onCommit = useCallback(
    (c: DragCommit) => {
      announce(c.id, "rect");
      if (c.kind === "move") edit?.onMove?.(c.id, c.to, "pointer");
      else edit?.onResize?.(c.id, c.size, "pointer");
    },
    [edit, announce],
  );
  const { onHandlePointerDown, onResizePointerDown, drag } = useDragResize({ gridRef, cols, items, onCommit });

  // A card of the widget gallery dropped on the grid (HTML5 drag and drop; foreign payloads are ignored).
  const onGalleryDragOver = useCallback(
    (e: DragEvent<HTMLElement>) => {
      if (!edit?.onDropWidget || !Array.from(e.dataTransfer?.types ?? []).includes(WIDGET_DRAG_TYPE)) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
    },
    [edit],
  );
  const onGalleryDrop = useCallback(
    (e: DragEvent<HTMLElement>) => {
      const grid = gridRef.current;
      if (!edit?.onDropWidget || !grid || !Array.from(e.dataTransfer?.types ?? []).includes(WIDGET_DRAG_TYPE)) return;
      e.preventDefault();
      const key = e.dataTransfer.getData(WIDGET_DRAG_TYPE);
      if (!key) return;
      const box = grid.getBoundingClientRect();
      const rtl = getComputedStyle(grid).direction === "rtl";
      const x = rtl ? box.right - e.clientX : e.clientX - box.left;
      edit.onDropWidget(key, pixelsToCell({ x, y: e.clientY - box.top }, { width: box.width, cols }));
    },
    [edit, cols],
  );

  const remove = useCallback(
    (id: string) => {
      const i = widgets.findIndex((w) => w.id === id);
      const next = widgets[i + 1] ?? widgets[i - 1];
      setMessage(t("dashboards.edit.removed", { title: titleOf(id) }));
      focusNext.current = next ? { id: next.id } : null;
      edit?.onRemove?.(id);
      if (!next) gridRef.current?.focus();
    },
    [widgets, edit, t, titleOf],
  );
  const duplicate = useCallback(
    (id: string) => {
      setMessage(t("dashboards.edit.duplicated", { title: titleOf(id) }));
      edit?.onDuplicate?.(id);
    },
    [edit, t, titleOf],
  );
  const shift = useCallback(
    (id: string, dir: "up" | "down") => {
      announce(id, "order");
      focusNext.current = { id, prefer: `[data-shift="${dir}"]` };
      edit?.onShift?.(id, dir);
    },
    [edit, announce],
  );

  const onCellKey = (w: EditWidget, rect: Rect | undefined) => (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (e.target !== e.currentTarget || drag || e.ctrlKey || e.metaKey || e.altKey) return;
    const a = keyToAction(e);
    if (!a) return;
    if (a.kind === "settings") {
      if (!edit?.onOpenSettings) return;
      e.preventDefault();
      edit.onOpenSettings(w.id);
      return;
    }
    if (!wide || !rect) {
      // phone: ↑/↓ change the order, nothing else moves
      if (a.kind === "move" && a.dx === 0 && edit?.onShift) {
        const i = widgets.findIndex((x) => x.id === w.id);
        const j = i + a.dy;
        e.preventDefault();
        if (j >= 0 && j < widgets.length) shift(w.id, a.dy < 0 ? "up" : "down");
      }
      return;
    }
    e.preventDefault();
    const rtl = gridRef.current ? getComputedStyle(gridRef.current).direction === "rtl" : false;
    const dx = rtl ? -a.dx : a.dx;
    announce(w.id, "rect");
    if (a.kind === "move") edit?.onMove?.(w.id, { x: rect.x + dx, y: rect.y + a.dy }, "keyboard");
    else edit?.onResize?.(w.id, { w: rect.w + dx, h: rect.h + a.dy }, "keyboard");
  };

  const editChrome = (w: EditWidget): CellEdit["chrome"] => ({
    handle: wide ? (
      <span
        data-testid={`widget-${w.id}-handle`}
        aria-hidden="true"
        onPointerDown={onHandlePointerDown(w.id)}
        className="-my-1.5 -ms-1.5 shrink-0 cursor-grab touch-none select-none px-1.5 py-1.5 text-sm tracking-[-2px] text-muted active:cursor-grabbing"
      >
        ⋮⋮
      </span>
    ) : undefined,
    actions: wide ? (
      <span className="flex shrink-0 items-center gap-1">
        {edit?.onOpenSettings && (
          <button
            type="button"
            aria-label={t("dashboards.edit.settings")}
            title={t("dashboards.edit.settings")}
            onClick={() => edit.onOpenSettings?.(w.id)}
            className={ICON_BTN}
          >
            <GearIcon />
          </button>
        )}
        {edit?.onDuplicate && (
          <button
            type="button"
            aria-label={t("dashboards.edit.duplicate")}
            title={edit.canDuplicate === false ? t("dashboards.edit.limitReached", { count: MAX_WIDGETS_PER_DASHBOARD }) : t("dashboards.edit.duplicate")}
            disabled={edit.canDuplicate === false}
            onClick={() => duplicate(w.id)}
            className={ICON_BTN}
          >
            <DuplicateIcon />
          </button>
        )}
        {edit?.onRemove && (
          <button
            type="button"
            aria-label={t("dashboards.edit.remove")}
            title={t("dashboards.edit.remove")}
            onClick={() => remove(w.id)}
            className={cn(ICON_BTN, "text-danger hover:bg-danger/10 hover:text-danger")}
          >
            <RemoveIcon />
          </button>
        )}
      </span>
    ) : (
      // phone: the controls row below the tile replaces the header buttons
      <></>
    ),
  });

  const cell = (w: EditWidget, index: number) => {
    let rect = rects?.get(w.id) ?? w.layout.lg;
    let style: CSSProperties | undefined;
    let cellEdit: CellEdit | undefined;
    if (editing) {
      const dragged = drag?.id === w.id;
      if (drag && !(dragged && drag.kind === "move")) rect = drag.preview.get(w.id) ?? rect;
      const invalid = edit?.invalidWidgetId === w.id;
      cellEdit = {
        chrome: editChrome(w),
        attrs: {
          tabIndex: 0,
          role: "group",
          "aria-roledescription": t("dashboards.edit.widgetRole"),
          onKeyDown: onCellKey(w, rects?.get(w.id)),
        },
        className: cn(
          "relative rounded-[14px] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
          invalid && "ring-2 ring-danger",
          dragged && "z-20 shadow-2xl ring-2 ring-accent",
          !wide && "flex flex-col gap-2",
        ),
        after: wide ? (
          // 24 px hit area (touch) around the 12 px corner mark
          <span
            data-testid={`widget-${w.id}-resize`}
            aria-hidden="true"
            onPointerDown={onResizePointerDown(w.id)}
            className="absolute bottom-0 end-0 z-10 flex h-6 w-6 cursor-nwse-resize touch-none items-end justify-end p-1"
          >
            <span className="h-3 w-3 rounded-ee border-b-2 border-e-2 border-accent" />
          </span>
        ) : (
          <PhoneControls
            widgetId={w.id}
            isFirst={index === 0}
            isLast={index === widgets.length - 1}
            onShift={(dir) => shift(w.id, dir)}
            onSettings={() => edit?.onOpenSettings?.(w.id)}
          />
        ),
      };
      if (dragged && drag) {
        style = {
          opacity: 0.96,
          transform:
            drag.kind === "move"
              ? `translate(${drag.ghost.dx}px, ${drag.ghost.dy}px)${reducedMotion ? "" : " rotate(-0.6deg)"}`
              : undefined,
        };
      }
    }
    if (rects) style = { ...style, gridColumn: `${rect.x + 1} / span ${rect.w}`, gridRow: `${rect.y + 1} / span ${rect.h}` };
    const props = {
      widget: w,
      def: defByKey.get(w.type_key),
      rect,
      mode,
      canEdit,
      onRetry,
      edit,
      onConfigure: canEdit ? onConfigureWidget : undefined,
      cellEdit,
      style,
    };
    return editing && edit?.previewIds?.has(w.id) ? (
      <PreviewGridCell key={w.id} {...props} />
    ) : (
      <GridCell key={w.id} {...props} state={data[w.id] ?? NONE_STATE} />
    );
  };

  const live = editing && (
    <>
      <div role="status" aria-live="polite" data-testid="edit-announcer" className="sr-only">
        {message}
      </div>
      <p className="mx-1 mt-3.5 text-xs text-muted">{wide ? t("dashboards.edit.keyboardHint") : t("dashboards.edit.phoneHint")}</p>
    </>
  );

  if (!wide) {
    // Phone: array order; runs of two or more counters share a two-column row (not while editing:
    // every widget then carries its own order controls).
    const blocks: (EditWidget | EditWidget[])[] = [];
    for (const w of widgets) {
      const last = blocks[blocks.length - 1];
      if (!editing && w.type_key === COUNTER && Array.isArray(last)) last.push(w);
      else if (!editing && w.type_key === COUNTER) blocks.push([w]);
      else blocks.push(w);
    }
    const indexOf = new Map(widgets.map((w, i) => [w.id, i]));
    return (
      <>
        <div
          ref={gridRef}
          tabIndex={editing ? -1 : undefined}
          data-testid="dashboard-grid"
          data-breakpoint="sm"
          data-dashboard-id={dashboardId}
          data-editing={editing ? "true" : undefined}
          className="flex flex-col gap-3 outline-none"
        >
          {blocks.map((block) =>
            Array.isArray(block) && block.length > 1 ? (
              <div key={block[0].id} data-testid="counter-group" className="grid grid-cols-2 gap-2.5">
                {block.map((w) => cell(w, indexOf.get(w.id)!))}
              </div>
            ) : (
              cell(Array.isArray(block) ? block[0] : block, indexOf.get((Array.isArray(block) ? block[0] : block).id)!)
            ),
          )}
        </div>
        {live}
      </>
    );
  }

  const gridStyle: CSSProperties = {
    display: "grid",
    gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
    gridAutoRows: `${ROW_H}px`,
    gap: `${GAP}px`,
  };
  if (!editing) {
    return (
      <div data-testid="dashboard-grid" data-breakpoint={breakpoint} data-dashboard-id={dashboardId} style={gridStyle}>
        {widgets.map(cell)}
      </div>
    );
  }
  const target = drag?.target;
  return (
    <>
      <div className="rounded-xl p-2 outline-dashed outline-1 outline-border">
        <div
          ref={gridRef}
          tabIndex={-1}
          data-testid="dashboard-grid"
          data-breakpoint={breakpoint}
          data-dashboard-id={dashboardId}
          data-editing="true"
          className="outline-none"
          onDragOver={onGalleryDragOver}
          onDrop={onGalleryDrop}
          style={{
            ...gridStyle,
            // the column guide: one tinted stripe per column
            backgroundImage: `linear-gradient(to right, ${GUIDE_TINT} 0, ${GUIDE_TINT} calc(100% - ${GAP}px), transparent calc(100% - ${GAP}px))`,
            backgroundSize: `calc((100% + ${GAP}px) / ${cols}) 100%`,
          }}
        >
          {target && (
            <div
              data-testid="drop-target"
              aria-hidden="true"
              className="pointer-events-none rounded-[14px] border-2 border-dashed border-accent bg-accent/10"
              style={{ gridColumn: `${target.x + 1} / span ${target.w}`, gridRow: `${target.y + 1} / span ${target.h}` }}
            />
          )}
          {widgets.map(cell)}
        </div>
      </div>
      {live}
    </>
  );
}
