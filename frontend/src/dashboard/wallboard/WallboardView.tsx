import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { cn } from "@/lib/cn";
import type { DashboardFilters } from "@/lib/dashboardsApi";
import { DashboardGrid } from "../DashboardGrid";
import type { WidgetDataState } from "../data";
import { DashboardFiltersProvider } from "../filters/filtersContext";
import type { EditWidget } from "../model";
import { WALLBOARD_GAP, wallboardHeaderPx, wallboardPadding, wallboardRowHeight, wallboardRows } from "./fit";
import { useViewport } from "./useViewport";
import { usePrefersReducedMotion } from "./useReducedMotion";
import { WallboardHeader } from "./WallboardHeader";

export interface WallboardViewProps {
  name: string;
  widgets: EditWidget[];
  data: Record<string, WidgetDataState>;
  filters: DashboardFilters;
  /** ISO time of the last successful data. */
  generatedAt: string | null;
  /** The last success is older than three refresh intervals. */
  stale: boolean;
  position?: { index: number; count: number };
  onRetry: (id: string) => void;
  /** Absent on the public page (no drill links there anyway). */
  dashboardId?: number;
  /** Extra content over the board (TV mode's control cluster). */
  overlay?: ReactNode;
}

/**
 * One dashboard as a wallboard: dark, the whole `lg` layout in one viewport (row height from
 * `wallboardRowHeight`, never scrolls), a header with the name, the age of the data and a clock.
 * Fades in over 300 ms unless the viewer prefers reduced motion.
 */
export function WallboardView({ name, widgets, data, filters, generatedAt, stale, position, onRetry, dashboardId, overlay }: WallboardViewProps) {
  const viewport = useViewport();
  const reduced = usePrefersReducedMotion();
  const [shown, setShown] = useState(reduced);
  useEffect(() => {
    if (reduced) {
      setShown(true);
      return;
    }
    const frame = requestAnimationFrame(() => setShown(true));
    return () => cancelAnimationFrame(frame);
  }, [reduced]);

  const pad = wallboardPadding(viewport);
  const headerPx = wallboardHeaderPx(viewport);
  const rows = wallboardRows(widgets);
  const rowHeight = wallboardRowHeight(viewport, rows, headerPx);
  const vars = {
    padding: `${pad.y}px ${pad.x}px`,
    "--wb-scale": rowHeight / 64,
    "--wb-zoom": Math.min(1.5, Math.max(0.75, rowHeight / 128)),
    "--wb-head": Math.max(0.5, viewport.width / 1920),
  } as CSSProperties;
  return (
    <div
      data-theme="dark"
      data-testid="wallboard"
      data-row-height={rowHeight}
      className={cn(
        "relative flex h-full w-full flex-col gap-0 overflow-hidden bg-bg text-ink",
        reduced ? undefined : "transition-opacity duration-300",
        shown ? "opacity-100" : "opacity-0",
      )}
      style={vars}
    >
      <WallboardHeader name={name} generatedAt={generatedAt} stale={stale} position={position} heightPx={headerPx} />
      <DashboardFiltersProvider filters={filters}>
        <div className="min-h-0 flex-1 overflow-hidden" data-testid="wallboard-board">
          {widgets.length > 0 && (
            <DashboardGrid
              widgets={widgets}
              mode="wallboard"
              breakpoint="lg"
              dashboardId={dashboardId}
              canEdit={false}
              data={data}
              onRetry={onRetry}
              rowHeight={rowHeight}
              gap={WALLBOARD_GAP}
            />
          )}
        </div>
      </DashboardFiltersProvider>
      {overlay}
    </div>
  );
}
