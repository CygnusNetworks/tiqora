/**
 * Widget data reaches components through a context, not through `WidgetProps` (the shared
 * contract has no data field). The host wraps each placed widget in `WidgetDataProvider` (fed by
 * the batched `/dashboards/{id}/data` result); the settings drawer feeds it from the preview route.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  dashboardKeys,
  dashboardsApi,
  settingsHash,
  type DashboardData,
  type DashboardFilters,
  type WidgetResult,
} from "@/lib/dashboardsApi";
import { subscribeSSE } from "@/lib/useSSE";
import { useDashboardWidgetDefinitions, type WidgetDefinition } from "@/plugins/dashboardWidgets";
import { FILTER_AWARE_KEYS } from "./definitions";
import { useDashboardFilters } from "./filters/filtersContext";
import type { EditWidget } from "./model";

export type WidgetDataStatus = "loading" | "ok" | "pending" | "error" | "forbidden" | "none";

export interface WidgetDataValue<T = unknown> {
  status: WidgetDataStatus;
  data?: T;
  /** German, user-presentable (from the server) */
  error?: string;
  /** Refetch only this widget. */
  refetch: () => void;
}

const NONE: WidgetDataValue = { status: "none", refetch: () => {} };

const WidgetDataContext = createContext<WidgetDataValue>(NONE);

export function WidgetDataProvider({ value, children }: { value: WidgetDataValue; children: ReactNode }) {
  return <WidgetDataContext.Provider value={value}>{children}</WidgetDataContext.Provider>;
}

/** The data of the surrounding widget; `status: "none"` outside a provider (never throws). */
export function useWidgetData<T = unknown>(): {
  status: WidgetDataStatus;
  data: T | undefined;
  error?: string;
  refetch: () => void;
} {
  const v = useContext(WidgetDataContext);
  return { status: v.status, data: v.data as T | undefined, error: v.error, refetch: v.refetch };
}

// ---------------------------------------------------------------- data hooks of the host page

/** One widget's data as the grid hands it to `WidgetDataProvider`. */
export type WidgetDataState = { status: WidgetDataStatus; data?: unknown; error?: string };

/**
 * Pauses before each single-widget fetch of a widget the batch answered `pending`. They grow and
 * are capped, so a slow source is not hammered (the server's global budget answers `pending` under
 * load); after the last one the widget shows an error with "Erneut versuchen".
 */
export const PENDING_RETRY_DELAYS_MS: readonly number[] = [2000, 4000, 8000, 16000, 30000, 30000];
/** SSE events are collected this long before one batch request. */
export const SSE_COALESCE_MS = 2000;
/** Minimum distance between two SSE-triggered batch requests of one dashboard. */
export const SSE_MIN_SPACING_MS = 10_000;

function toState(result: WidgetResult): WidgetDataState {
  return { status: result.status, data: result.data ?? undefined, error: result.error ?? undefined };
}

function isHidden(): boolean {
  return typeof document !== "undefined" && document.hidden === true;
}

function hasData(def: WidgetDefinition | undefined): boolean {
  return def !== undefined && def.dataEndpoint != null;
}

/** `interval:N` entries of `refreshOn` (seconds, positive integers). */
function intervalsOf(def: WidgetDefinition): number[] {
  return def.refreshOn
    .map((r) => /^interval:(\d+)$/.exec(r))
    .filter((m): m is RegExpExecArray => m !== null)
    .map((m) => Number(m[1]))
    .filter((n) => n > 0);
}

interface Engine {
  /** Start polling these pending widgets (no-op for ones already polled); stop all others. */
  syncPending(pendingIds: string[]): void;
  refetch(widgetId: string): void;
  fetchIds(ids: string[]): void;
}

/**
 * Data of one dashboard view: one `GET /dashboards/{id}/data` (query key `dashboardKeys.data(id)`),
 * then a single-widget fetch for every widget the batch answered `pending` (with growing pauses,
 * never while the page is hidden). Live refresh: SSE events listed in a widget's `refreshOn` are
 * coalesced (2 s, at least 10 s between two batches, held while hidden and sent once on return);
 * `interval:N` starts one timer per distinct N. Fresh results are merged into the cached batch.
 * With `enabled: false` (edit mode) nothing is requested and cached data stays.
 */
export function useDashboardData(
  dashboardId: number,
  widgets: EditWidget[],
  opts: { enabled: boolean },
): { byWidget: Record<string, WidgetDataState>; refetch: (widgetId: string) => void } {
  const { enabled } = opts;
  const { t } = useTranslation();
  const qc = useQueryClient();
  const defs = useDashboardWidgetDefinitions();
  const defByKey = useMemo(() => new Map(defs.map((d) => [d.key, d])), [defs]);
  const dataWidgets = useMemo(
    () => widgets.filter((w) => hasData(defByKey.get(w.type_key))),
    [widgets, defByKey],
  );
  const [refetching, setRefetching] = useState<ReadonlySet<string>>(() => new Set());

  const batch = useQuery({
    queryKey: dashboardKeys.data(dashboardId),
    queryFn: ({ signal }) => dashboardsApi.data(dashboardId, undefined, signal),
    enabled: enabled && dataWidgets.length > 0,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
    retry: false,
  });

  // Read by the engine's timers and listeners, so they always see the current widgets.
  const live = useRef({ dataWidgets, defByKey, tooSlow: "" });
  live.current = { dataWidgets, defByKey, tooSlow: t("dashboards.view.tooSlow") };
  const engineRef = useRef<Engine | null>(null);

  useEffect(() => {
    if (!enabled) return;
    const life = new AbortController();
    const key = dashboardKeys.data(dashboardId);
    // Only into an existing batch: a partial entry would turn a failed or still running batch into
    // "success" and every widget missing from `fresh` would show an empty state instead of
    // error/loading. Without an entry the (re)fetched full batch brings these widgets anyway.
    const merge = (fresh: Record<string, WidgetResult>) => {
      if (life.signal.aborted) return;
      qc.setQueryData<DashboardData>(key, (old) => old && { widgets: { ...old.widgets, ...fresh } });
    };
    const fetchOne = async (widgetId: string): Promise<WidgetResult | null> => {
      try {
        return await dashboardsApi.widgetData(dashboardId, widgetId, life.signal);
      } catch {
        return life.signal.aborted ? null : { status: "error" };
      }
    };

    // -- pending polls
    type Poll = { attempt: number; timer: ReturnType<typeof setTimeout> | null; held: boolean };
    const polls = new Map<string, Poll>();
    const stopPoll = (widgetId: string) => {
      const poll = polls.get(widgetId);
      if (poll?.timer) clearTimeout(poll.timer);
      polls.delete(widgetId);
    };
    const schedulePoll = (widgetId: string, attempt: number) => {
      const poll: Poll = { attempt, timer: null, held: false };
      poll.timer = setTimeout(() => void firePoll(widgetId, poll), PENDING_RETRY_DELAYS_MS[attempt]);
      polls.set(widgetId, poll);
    };
    const firePoll = async (widgetId: string, poll: Poll) => {
      poll.timer = null;
      if (isHidden()) {
        poll.held = true;
        return;
      }
      const result = await fetchOne(widgetId);
      if (!result || polls.get(widgetId) !== poll) return;
      if (result.status !== "pending") {
        polls.delete(widgetId);
        merge({ [widgetId]: result });
      } else if (poll.attempt + 1 < PENDING_RETRY_DELAYS_MS.length) {
        schedulePoll(widgetId, poll.attempt + 1);
      } else {
        polls.delete(widgetId);
        merge({ [widgetId]: { status: "error", error: live.current.tooSlow } });
      }
    };

    // -- batched refreshes (SSE and intervals)
    let queued = new Set<string>();
    let held = new Set<string>();
    let sseTimer: ReturnType<typeof setTimeout> | null = null;
    let lastSseBatch = -Infinity;
    const fetchIds = async (ids: string[]) => {
      if (ids.length === 0) return;
      try {
        merge((await dashboardsApi.data(dashboardId, ids, life.signal)).widgets);
      } catch {
        // keep showing the last data; the next event or tick tries again
      }
    };
    const flush = () => {
      sseTimer = null;
      const ids = [...queued];
      queued = new Set();
      if (isHidden()) {
        for (const id of ids) held.add(id);
        return;
      }
      if (ids.length === 0) return;
      lastSseBatch = Date.now();
      void fetchIds(ids);
    };
    const queue = (ids: string[]) => {
      if (ids.length === 0) return;
      if (isHidden()) {
        for (const id of ids) held.add(id);
        return;
      }
      for (const id of ids) queued.add(id);
      if (sseTimer) return;
      const delay = Math.max(SSE_COALESCE_MS, lastSseBatch + SSE_MIN_SPACING_MS - Date.now());
      sseTimer = setTimeout(flush, delay);
    };
    const unsubscribe = subscribeSSE((message) => {
      queue(
        live.current.dataWidgets
          .filter((w) => live.current.defByKey.get(w.type_key)?.refreshOn.includes(message.type))
          .map((w) => w.id),
      );
    });
    const onVisibility = () => {
      if (isHidden()) return;
      for (const [widgetId, poll] of polls) {
        if (poll.held) {
          poll.held = false;
          void firePoll(widgetId, poll);
        }
      }
      const ids = [...held];
      held = new Set();
      queue(ids);
    };
    document.addEventListener("visibilitychange", onVisibility);

    engineRef.current = {
      syncPending(pendingIds) {
        for (const widgetId of [...polls.keys()]) if (!pendingIds.includes(widgetId)) stopPoll(widgetId);
        for (const widgetId of pendingIds) if (!polls.has(widgetId)) schedulePoll(widgetId, 0);
      },
      refetch(widgetId) {
        if (qc.getQueryData(key) === undefined) {
          // the batch itself failed: one request for the whole dashboard again
          void qc.refetchQueries({ queryKey: key, exact: true });
          return;
        }
        stopPoll(widgetId);
        setRefetching((prev) => new Set(prev).add(widgetId));
        void fetchOne(widgetId).then((result) => {
          if (life.signal.aborted) return;
          setRefetching((prev) => {
            const next = new Set(prev);
            next.delete(widgetId);
            return next;
          });
          if (result) merge({ [widgetId]: result });
        });
      },
      fetchIds(ids) {
        if (isHidden()) {
          for (const id of ids) held.add(id);
          return;
        }
        void fetchIds(ids);
      },
    };
    return () => {
      life.abort();
      unsubscribe();
      document.removeEventListener("visibilitychange", onVisibility);
      if (sseTimer) clearTimeout(sseTimer);
      for (const widgetId of [...polls.keys()]) stopPoll(widgetId);
      engineRef.current = null;
      setRefetching(new Set());
    };
  }, [dashboardId, enabled, qc]);

  // Poll exactly the widgets the cache currently has as `pending`.
  const batchData = batch.data;
  useEffect(() => {
    const pendingIds = dataWidgets
      .filter((w) => batchData?.widgets[w.id]?.status === "pending")
      .map((w) => w.id);
    engineRef.current?.syncPending(pendingIds);
  }, [batchData, dataWidgets, enabled, dashboardId]);

  // One timer per distinct `interval:N`.
  const intervalKey = useMemo(() => {
    const seconds = new Set<number>();
    for (const w of dataWidgets) for (const n of intervalsOf(defByKey.get(w.type_key)!)) seconds.add(n);
    return [...seconds].sort((a, b) => a - b).join(",");
  }, [dataWidgets, defByKey]);
  useEffect(() => {
    if (!enabled || intervalKey === "") return;
    const timers = intervalKey.split(",").map((raw) => {
      const seconds = Number(raw);
      return setInterval(() => {
        const ids = live.current.dataWidgets
          .filter((w) => intervalsOf(live.current.defByKey.get(w.type_key)!).includes(seconds))
          .map((w) => w.id);
        // while hidden the engine holds the ids for the one refresh on return
        engineRef.current?.fetchIds(ids);
      }, seconds * 1000);
    });
    return () => timers.forEach(clearInterval);
  }, [enabled, intervalKey, dashboardId]);

  const refetch = useCallback((widgetId: string) => engineRef.current?.refetch(widgetId), []);

  const byWidget = useMemo(() => {
    const out: Record<string, WidgetDataState> = {};
    for (const w of widgets) {
      const entry = batchData?.widgets[w.id];
      if (!hasData(defByKey.get(w.type_key))) out[w.id] = { status: "none" };
      else if (refetching.has(w.id)) out[w.id] = { status: "loading" };
      else if (entry) out[w.id] = toState(entry);
      // not in the batch yet (e.g. added in edit mode): a skeleton until the refetch answers
      else if (batchData) out[w.id] = batch.isFetching ? { status: "loading" } : { status: "none" };
      else if (batch.isError && !batch.isFetching) out[w.id] = { status: "error" };
      else out[w.id] = { status: "loading" };
    }
    return out;
  }, [widgets, defByKey, batchData, batch.isError, batch.isFetching, refetching]);

  return { byWidget, refetch };
}

/**
 * Data for unsaved settings (settings drawer, edit mode): `POST /dashboard-widget-types/{key}/data`
 * once the settings stayed unchanged for `debounceMs`; equal settings share one cache entry. Types
 * without data (and `enabled: false`) are `none` without a request.
 */
export function usePreviewData(
  typeKey: string,
  settings: Record<string, unknown>,
  opts: { enabled?: boolean; debounceMs?: number; pinned?: boolean } = {},
): WidgetDataState & { refetch: () => void } {
  const { enabled = true, debounceMs = 300, pinned = false } = opts;
  // The open dashboard's filters narrow the preview like the saved widget (filter-aware, unpinned types).
  const dashboardFilters = useDashboardFilters();
  const sentFilters = dashboardFilters.active && !pinned && FILTER_AWARE_KEYS.includes(typeKey) ? dashboardFilters.filters : undefined;
  const filtersHash = sentFilters ? JSON.stringify(sentFilters) : "";
  const def = useDashboardWidgetDefinitions().find((d) => d.key === typeKey);
  const active = enabled && hasData(def);
  const hash = settingsHash(settings) + filtersHash;
  const latest = useRef(settings);
  const latestFilters = useRef(sentFilters);
  latestFilters.current = sentFilters;
  latest.current = settings;
  const [settled, setSettled] = useState<{ hash: string; settings: Record<string, unknown>; filters: DashboardFilters | undefined } | null>(null);
  useEffect(() => {
    if (!active) return;
    const timer = setTimeout(() => setSettled({ hash, settings: latest.current, filters: latestFilters.current }), debounceMs);
    return () => clearTimeout(timer);
  }, [active, hash, debounceMs]);

  const query = useQuery({
    queryKey: dashboardKeys.preview(typeKey, settled?.hash ?? ""),
    queryFn: ({ signal }) => settled!.filters
        ? dashboardsApi.previewData(typeKey, settled!.settings, signal, settled!.filters)
        : dashboardsApi.previewData(typeKey, settled!.settings, signal),
    enabled: active && settled !== null,
    staleTime: 30_000,
    placeholderData: (previous) => previous,
    retry: false,
  });
  const queryRefetch = query.refetch;
  const refetch = useCallback(() => void queryRefetch(), [queryRefetch]);
  if (!active) return { status: "none", refetch };
  if (query.data) return { ...toState(query.data), refetch };
  if (query.isError) return { status: "error", refetch };
  return { status: "loading", refetch };
}

// ---------------------------------------------------------------- edit mode (R-DASH-PREVIEW-BUDGET)

const contentKey = (w: Pick<EditWidget, "type_key" | "type_version" | "settings">) =>
  `${w.type_key}@${w.type_version}:${settingsHash(w.settings)}`;

/**
 * Data of the widgets while editing. A widget with the same type, version and settings as one the
 * view loaded keeps that widget's batch result (moving, resizing, duplicating and undoing never
 * fetch); a new or changed data widget is listed in `previewIds` and fetched through
 * `usePreviewData`. Finished results (`ok`, `forbidden`) are reused, and so is a batch still in
 * flight (`loading`: the plan is recomputed when it lands). `pending` and `error` get one preview.
 */
export function editDataPlan(
  saved: EditWidget[],
  savedStates: Record<string, WidgetDataState>,
  current: EditWidget[],
  hasDataEndpoint: (typeKey: string) => boolean,
): { byWidget: Record<string, WidgetDataState>; previewIds: ReadonlySet<string> } {
  const reusable = new Map<string, WidgetDataState>();
  for (const w of saved) {
    const state = savedStates[w.id];
    if (state && (state.status === "ok" || state.status === "forbidden" || state.status === "loading")) {
      reusable.set(contentKey(w), state);
    }
  }
  const byWidget: Record<string, WidgetDataState> = {};
  const previewIds = new Set<string>();
  for (const w of current) {
    if (!hasDataEndpoint(w.type_key)) {
      byWidget[w.id] = { status: "none" };
      continue;
    }
    const reuse = reusable.get(contentKey(w));
    if (reuse) byWidget[w.id] = reuse;
    else previewIds.add(w.id);
  }
  return { byWidget, previewIds };
}
