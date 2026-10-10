import { Component, useMemo, type CSSProperties, type ErrorInfo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useOptionalAuth } from "@/auth/AuthContext";
import { usePlugins } from "@/plugins/PluginProvider";
import {
  useDashboardWidgetDefinitions,
  type WidgetDefinition,
  type WidgetMode,
} from "@/plugins/dashboardWidgets";
import { WidgetChromeProvider, WidgetFrame } from "./chrome/WidgetFrame";
import { WidgetForbidden } from "./chrome/WidgetError";
import { WidgetSkeleton } from "./chrome/WidgetSkeleton";
import { WidgetUnavailable } from "./chrome/WidgetUnavailable";
import { skeletonRows } from "./chrome/mode";
import { WidgetDataProvider, type WidgetDataState } from "./data";
import { canUseWidget, settingsWithDefaults } from "./definitions";
import { layoutForBreakpoint } from "./layout/breakpoints";
import { COLS, GAP, ROW_H, type Breakpoint, type EditWidget, type Rect } from "./model";

/** Edit-mode callbacks of the grid (Task 12 adds drag, resize and the widget header buttons). */
export interface EditHandlers {
  /** "Entfernen" on a widget (also on the tile of an unavailable widget type). */
  onRemove?: (id: string) => void;
}

const NO_FILTERS: Record<string, unknown> = {};
const COUNTER = "core.counter";

/** Catches a widget that throws while rendering, so one broken widget never takes the page down. */
class CellBoundary extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
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

function GridCell({
  widget,
  def,
  state,
  rect,
  mode,
  canEdit,
  onRetry,
  edit,
  style,
}: {
  widget: EditWidget;
  def: WidgetDefinition | undefined;
  state: WidgetDataState;
  rect: Rect;
  mode: WidgetMode;
  canEdit: boolean;
  onRetry: (id: string) => void;
  edit?: EditHandlers;
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
  const waiting = state.status === "loading" || state.status === "pending";
  const size = { w: rect.w, h: rect.h };

  let body: ReactNode;
  if (!def) {
    // Plugin types register once the plugin list arrived; until then the slot only waits.
    body =
      !pluginsReady && !widget.type_key.startsWith("core.") ? (
        <PlainTile>
          <WidgetSkeleton rows={skeletonRows(rect.h)} />
        </PlainTile>
      ) : (
        <WidgetUnavailable
          typeKey={widget.type_key}
          mode={mode}
          onRemove={edit?.onRemove ? () => edit.onRemove?.(id) : undefined}
        />
      );
  } else if (!canUseWidget(def, { is_admin: user?.is_admin === true, can_view_reports: user?.can_view_reports === true })) {
    body = (
      <WidgetFrame title={widget.title_override?.trim() || t(def.titleKey, { ns: def.ns })} mode={mode} testId="widget-forbidden">
        <WidgetForbidden message={t("dashboards.state.forbidden")} />
      </WidgetFrame>
    );
  } else {
    const Widget = def.component;
    const content = (
      <Widget instanceId={id} settings={settings} size={size} filters={NO_FILTERS} mode={mode} />
    );
    body = (
      <WidgetChromeProvider value={{ titleOverride: widget.title_override, canEdit }}>
        <WidgetDataProvider value={{ ...state, refetch: () => onRetry(id) }}>
          {def.owner === "core" ? (
            content
          ) : (
            // Core widgets draw their own frame; plugin widgets get the host's (PluginBoundary is inside the component).
            <WidgetFrame
              title={t(def.titleKey, { ns: def.ns, defaultValue: def.key })}
              mode={mode}
              testId={`plugin-widget-${def.key}`}
            >
              {content}
            </WidgetFrame>
          )}
        </WidgetDataProvider>
      </WidgetChromeProvider>
    );
  }

  return (
    <div
      data-testid={waiting ? `widget-${id}-skeleton` : `widget-${id}`}
      data-widget-id={id}
      data-type={widget.type_key}
      className="min-h-0 min-w-0"
      style={style}
    >
      <CellBoundary
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
    </div>
  );
}

/**
 * The widgets of one dashboard. `lg`/`md`: CSS grid of 12/8 columns, 64 px rows, 16 px gap, each
 * widget placed by `grid-column`/`grid-row` from its rect (clamped and overlap-free; RTL mirrors via
 * the grid's direction). `sm`: one column in array order, consecutive counters two per row.
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
}: {
  widgets: EditWidget[];
  mode: "view" | "edit";
  breakpoint: Breakpoint;
  dashboardId: number;
  canEdit: boolean;
  data: Record<string, WidgetDataState>;
  onRetry: (id: string) => void;
  edit?: EditHandlers;
}) {
  const defs = useDashboardWidgetDefinitions();
  const defByKey = useMemo(() => new Map(defs.map((d) => [d.key, d])), [defs]);
  const rects = useMemo(
    () =>
      breakpoint === "sm"
        ? null
        : layoutForBreakpoint(widgets, breakpoint, (key) => defByKey.get(key)?.size.min ?? [1, 1]),
    [widgets, breakpoint, defByKey],
  );

  const cell = (w: EditWidget) => {
    const rect = rects?.get(w.id) ?? w.layout.lg;
    return (
      <GridCell
        key={w.id}
        widget={w}
        def={defByKey.get(w.type_key)}
        state={data[w.id] ?? { status: "none" }}
        rect={rect}
        mode={mode}
        canEdit={canEdit}
        onRetry={onRetry}
        edit={edit}
        style={rects ? { gridColumn: `${rect.x + 1} / span ${rect.w}`, gridRow: `${rect.y + 1} / span ${rect.h}` } : undefined}
      />
    );
  };

  if (breakpoint === "sm") {
    // Phone: array order; runs of two or more counters share a two-column row.
    const blocks: (EditWidget | EditWidget[])[] = [];
    for (const w of widgets) {
      const last = blocks[blocks.length - 1];
      if (w.type_key === COUNTER && Array.isArray(last)) last.push(w);
      else if (w.type_key === COUNTER) blocks.push([w]);
      else blocks.push(w);
    }
    return (
      <div data-testid="dashboard-grid" data-breakpoint="sm" data-dashboard-id={dashboardId} className="flex flex-col gap-3">
        {blocks.map((block) =>
          Array.isArray(block) && block.length > 1 ? (
            <div key={block[0].id} data-testid="counter-group" className="grid grid-cols-2 gap-2.5">
              {block.map(cell)}
            </div>
          ) : (
            cell(Array.isArray(block) ? block[0] : block)
          ),
        )}
      </div>
    );
  }

  return (
    <div
      data-testid="dashboard-grid"
      data-breakpoint={breakpoint}
      data-dashboard-id={dashboardId}
      style={{
        display: "grid",
        gridTemplateColumns: `repeat(${COLS[breakpoint]}, minmax(0, 1fr))`,
        gridAutoRows: `${ROW_H}px`,
        gap: `${GAP}px`,
      }}
    >
      {widgets.map(cell)}
    </div>
  );
}
