import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { dashboardKeys, dashboardsApi } from "@/lib/dashboardsApi";
import { cn } from "@/lib/cn";
import { useDashboardData } from "@/dashboard/data";
import { NO_DASHBOARD_FILTERS } from "@/dashboard/filters/filtersContext";
import { toEditWidget } from "@/dashboard/model";
import { registerCoreWidgets } from "@/dashboard/widgets";
import { ROTATE_DEFAULT, WALLBOARD_REFRESH_SECONDS, WALLBOARD_STALE_INTERVALS, parseWallboardIds } from "@/dashboard/wallboard/search";
import { useRotation } from "@/dashboard/wallboard/useRotation";
import { WallboardHeader } from "@/dashboard/wallboard/WallboardHeader";
import { WallboardView } from "@/dashboard/wallboard/WallboardView";

registerCoreWidgets();

/** The control cluster hides this long after the last pointer move. */
const CONTROLS_IDLE_MS = 3000;

const CONTROL =
  "rounded-lg border border-hairline bg-surface/90 px-3.5 py-2 text-sm font-medium text-ink backdrop-blur transition-colors hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";

/** One dashboard of the rotation: its own data, its own failure (a broken one never stops the others). */
function WallboardDashboard({ id, position }: { id: number; position?: { index: number; count: number } }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const dashQ = useQuery({
    queryKey: dashboardKeys.one(id),
    queryFn: ({ signal }) => dashboardsApi.get(id, signal),
    retry: false,
    refetchInterval: 10 * 60_000,
  });
  const dashboard = dashQ.data;
  const widgets = useMemo(() => (dashboard ? dashboard.widgets.map(toEditWidget) : []), [dashboard]);
  const { byWidget, refetch } = useDashboardData(id, widgets, { enabled: dashboard !== undefined });

  // Observer only (never fetches by itself): the time of the last good batch.
  const batch = useQuery({
    queryKey: dashboardKeys.data(id),
    queryFn: ({ signal }) => dashboardsApi.data(id, undefined, signal),
    enabled: false,
  });
  const updatedAt = batch.dataUpdatedAt > 0 ? batch.dataUpdatedAt : null;

  // Heartbeat: a full refresh every 30 s; a failing one leaves the last data and ages the picture.
  const hasData = widgets.length > 0;
  useEffect(() => {
    if (!hasData) return;
    const timer = setInterval(() => {
      if (document.hidden) return;
      void qc.refetchQueries({ queryKey: dashboardKeys.data(id), exact: true });
    }, WALLBOARD_REFRESH_SECONDS * 1000);
    return () => clearInterval(timer);
  }, [qc, id, hasData]);

  const [now, setNow] = useState(() => Date.now());
  const [mountedAt] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 5000);
    return () => clearInterval(timer);
  }, []);
  // The rotation remounts this view with the dashboard's cached batch, which may be older than the
  // threshold when the cycle is long. It only counts as stale once a fetch after the mount settled
  // (a failure keeps the old picture), or when that fetch takes longer than the threshold itself.
  const staleAfterMs = WALLBOARD_STALE_INTERVALS * WALLBOARD_REFRESH_SECONDS * 1000;
  const stale =
    updatedAt !== null && now - updatedAt > staleAfterMs && (batch.isFetchedAfterMount || now - mountedAt > staleAfterMs);

  if (!dashboard) {
    return (
      <div className="flex h-full flex-col gap-6 bg-bg p-12" data-theme="dark">
        <WallboardHeader name={t("dashboards.wallboard.label")} generatedAt={null} stale={false} position={position} heightPx={96} />
        <p role={dashQ.isError ? "alert" : "status"} className="text-2xl text-muted" data-testid="wallboard-unavailable">
          {dashQ.isError ? t("dashboards.wallboard.loadFailed") : t("dashboards.wallboard.loading")}
        </p>
      </div>
    );
  }
  return (
    <WallboardView
      name={dashboard.name}
      widgets={widgets}
      data={byWidget}
      filters={dashboard.filters ?? NO_DASHBOARD_FILTERS}
      generatedAt={updatedAt === null ? null : new Date(updatedAt).toISOString()}
      stale={stale}
      position={position}
      onRetry={refetch}
      dashboardId={dashboard.id}
    />
  );
}

/** Keeps the screen awake while the TV mode is open (where the browser can; failures are ignored). */
function useWakeLock() {
  useEffect(() => {
    let lock: { release: () => Promise<void> } | null = null;
    let alive = true;
    const acquire = async () => {
      try {
        const wl = (navigator as Navigator & { wakeLock?: { request: (type: "screen") => Promise<{ release: () => Promise<void> }> } }).wakeLock;
        if (!wl || document.hidden) return;
        const got = await wl.request("screen");
        if (alive) lock = got;
        else void got.release().catch(() => {});
      } catch {
        // not allowed (battery saver, insecure origin): the screen may sleep, nothing else
      }
    };
    void acquire();
    const onVisibility = () => {
      if (!document.hidden) void acquire();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      alive = false;
      document.removeEventListener("visibilitychange", onVisibility);
      void lock?.release().catch(() => {});
    };
  }, []);
}

/**
 * TV mode of the agent app: the chosen dashboards (`?ids=7,9`, default all own tabs) one after the
 * other, `?rotate=` seconds each. Covers the whole window; "Vollbild" and "Beenden" appear on pointer
 * movement only. Keeps SSE and the refresh timers of the normal view; links stay (the viewer is logged in).
 */
export function WallboardPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as { ids?: unknown; rotate?: number };
  const requested = useMemo(() => parseWallboardIds(search.ids), [search.ids]);
  const listQ = useQuery({
    queryKey: dashboardKeys.list(),
    queryFn: ({ signal }) => dashboardsApi.list(signal),
    enabled: requested.length === 0,
  });
  const ids = useMemo(
    () => (requested.length > 0 ? requested : (listQ.data ?? []).slice(0, 10).map((tab) => tab.id)),
    [requested, listQ.data],
  );
  const { index } = useRotation(ids.length, search.rotate ?? ROTATE_DEFAULT);

  useWakeLock();
  const exit = useCallback(() => void navigate({ to: "/agent" }), [navigate]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !document.fullscreenElement) exit();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [exit]);

  const [controls, setControls] = useState(false);
  const idle = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onPointerMove = useCallback(() => {
    setControls(true);
    if (idle.current) clearTimeout(idle.current);
    idle.current = setTimeout(() => setControls(false), CONTROLS_IDLE_MS);
  }, []);
  useEffect(() => () => {
    if (idle.current) clearTimeout(idle.current);
  }, []);
  const [full, setFull] = useState(() => typeof document !== "undefined" && document.fullscreenElement != null);
  useEffect(() => {
    const onChange = () => setFull(document.fullscreenElement != null);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);
  const toggleFullscreen = () => {
    try {
      if (document.fullscreenElement) void document.exitFullscreen?.().catch(() => {});
      else void document.documentElement.requestFullscreen?.().catch(() => {});
    } catch {
      // no Fullscreen API: the window stays as it is
    }
  };

  const current = ids[index];
  return (
    <div
      data-testid="wallboard-page"
      data-theme="dark"
      onPointerMove={onPointerMove}
      className={cn("fixed inset-0 z-50 bg-bg text-ink", !controls && "cursor-none")}
    >
      {current !== undefined ? (
        <WallboardDashboard
          key={current}
          id={current}
          position={ids.length > 1 ? { index, count: ids.length } : undefined}
        />
      ) : (
        <p role="status" className="p-12 text-2xl text-muted" data-testid="wallboard-none">
          {listQ.isLoading ? t("dashboards.wallboard.loading") : t("dashboards.wallboard.none")}
        </p>
      )}
      <div
        data-testid="wallboard-controls"
        aria-hidden={!controls}
        className={cn(
          "absolute end-6 top-6 flex gap-2 transition-opacity motion-reduce:transition-none",
          controls ? "opacity-100" : "pointer-events-none opacity-0",
        )}
      >
        <button type="button" tabIndex={controls ? 0 : -1} onClick={toggleFullscreen} aria-pressed={full} className={CONTROL}>
          {t("dashboards.wallboard.fullscreen")}
        </button>
        <button type="button" tabIndex={controls ? 0 : -1} onClick={exit} className={CONTROL}>
          {t("dashboards.wallboard.exit")}
        </button>
      </div>
    </div>
  );
}
