import { useCallback, useEffect, useRef, useState } from "react";
import {
  kioskApi,
  KioskLinkInvalidError,
  KioskRetryableError,
  type KioskBoard,
  type KioskData,
} from "@/lib/kioskApi";
import type { WidgetDataState } from "../data";

/** The board (names, layouts, whitelisted settings) is re-read this often. */
export const BOARD_REFRESH_SECONDS = 600;
/** The next dashboard's data is fetched this long before the rotation switches to it. */
export const PREFETCH_LEAD_SECONDS = 5;
/** First pause after a failure; doubles per consecutive failure. */
export const BACKOFF_BASE_SECONDS = 15;
export const BACKOFF_MAX_SECONDS = 300;
/** The picture counts as stale after this many missed refresh intervals. */
export const STALE_INTERVALS = 3;

/** Seconds to wait after the `failures`-th consecutive failure (a server `Retry-After` is a floor). */
export function backoffSeconds(failures: number, retryAfter = 0): number {
  const grown = Math.min(BACKOFF_MAX_SECONDS, BACKOFF_BASE_SECONDS * 2 ** Math.max(0, failures - 1));
  return Math.max(grown, Math.min(BACKOFF_MAX_SECONDS, retryAfter));
}

function isAbort(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

function retryAfterOf(err: unknown): number {
  return err instanceof KioskRetryableError ? err.retryAfterSeconds : 0;
}

function isHidden(): boolean {
  return typeof document !== "undefined" && document.hidden === true;
}

export interface KioskBoardState {
  board: KioskBoard | null;
  /** The link is unknown, revoked or expired: final, nothing is polled any more. */
  invalid: boolean;
  /** The last board request failed (the previous board stays). */
  failing: boolean;
}

/** The board once per 10 minutes; a failing request keeps the last board and retries with backoff. */
export function useKioskBoard(token: string | null): KioskBoardState {
  const [state, setState] = useState<KioskBoardState>({ board: null, invalid: token === null, failing: false });
  useEffect(() => {
    if (token === null) {
      setState({ board: null, invalid: true, failing: false });
      return;
    }
    const life = new AbortController();
    let timer: ReturnType<typeof setTimeout> | null = null;
    let failures = 0;
    const run = async () => {
      try {
        const board = await kioskApi.board(token, life.signal);
        failures = 0;
        setState({ board, invalid: false, failing: false });
        timer = setTimeout(() => void run(), BOARD_REFRESH_SECONDS * 1000);
      } catch (err) {
        if (life.signal.aborted || isAbort(err)) return;
        if (err instanceof KioskLinkInvalidError) {
          setState({ board: null, invalid: true, failing: false });
          return;
        }
        failures += 1;
        setState((s) => ({ ...s, failing: true }));
        timer = setTimeout(() => void run(), backoffSeconds(failures, retryAfterOf(err)) * 1000);
      }
    };
    void run();
    return () => {
      life.abort();
      if (timer) clearTimeout(timer);
    };
  }, [token]);
  return state;
}

export interface KioskDashboardData {
  widgets: Record<string, WidgetDataState>;
  /** Browser time (ms) of the last good answer; the server clock is not trusted for ageing. */
  fetchedAt: number;
}

export interface KioskDataState {
  byDashboard: Record<number, KioskDashboardData>;
  /** The current dashboard has no fresh picture: a request failed or the last success is too old. */
  stale: boolean;
  /** A data request answered 404: the link died while the TV was running. */
  invalid: boolean;
  /** Fetch the current dashboard now (the stale banner's retry). */
  refresh: () => void;
}

/**
 * Merge a fresh answer into the previous picture: a widget that is `pending` (or missing) keeps its
 * last good value, so a wallboard that runs for hours never turns a tile into a permanent skeleton.
 */
export function mergeKioskData(
  previous: KioskDashboardData | undefined,
  fresh: KioskData,
): KioskDashboardData {
  const widgets: Record<string, WidgetDataState> = { ...(previous?.widgets ?? {}) };
  for (const [id, result] of Object.entries(fresh.widgets)) {
    const before = widgets[id];
    if (result.status === "pending" && before?.status === "ok") continue;
    widgets[id] = { status: result.status, data: result.data ?? undefined, error: result.error ?? undefined };
  }
  return { widgets, fetchedAt: Date.now() };
}

/**
 * Data of the wallboard: the current dashboard every `refresh_seconds`, the next one once, five
 * seconds before the rotation switches to it. No SSE. Failures keep the last picture and retry
 * with backoff; only a 404 ends the polling.
 */
export function useKioskData(
  token: string | null,
  board: KioskBoard | null,
  currentId: number | null,
  nextId: number | null,
): KioskDataState {
  const [byDashboard, setByDashboard] = useState<Record<number, KioskDashboardData>>({});
  const [failing, setFailing] = useState(false);
  const [invalid, setInvalid] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const refreshSeconds = board?.refresh_seconds ?? 30;
  const rotateSeconds = board?.rotate_seconds ?? 60;
  const failures = useRef(0);
  const kick = useRef<(() => void) | null>(null);
  const retryAfter = useRef(0);
  const fetchedAt = useRef(new Map<number, number>());
  const store = useCallback((id: number, fresh: KioskData) => {
    fetchedAt.current.set(id, Date.now());
    setByDashboard((all) => ({ ...all, [id]: mergeKioskData(all[id], fresh) }));
  }, []);

  const fetchOne = useCallback(
    async (id: number, signal: AbortSignal): Promise<"ok" | "retry" | "stop"> => {
      if (token === null) return "stop";
      try {
        const fresh = await kioskApi.data(token, id, signal);
        if (signal.aborted) return "stop";
        store(id, fresh);
        return "ok";
      } catch (err) {
        if (signal.aborted || isAbort(err)) return "stop";
        if (err instanceof KioskLinkInvalidError) {
          setInvalid(true);
          return "stop";
        }
        failures.current += 1;
        failures.current = Math.min(failures.current, 10);
        setFailing(true);
        retryAfter.current = retryAfterOf(err);
        return "retry";
      }
    },
    [token, store],
  );

  // The current dashboard: now, then every refresh interval (backoff while failing).
  useEffect(() => {
    if (token === null || currentId === null) return;
    const life = new AbortController();
    let timer: ReturnType<typeof setTimeout> | null = null;
    const run = async () => {
      if (isHidden()) {
        timer = setTimeout(() => void run(), 1000);
        return;
      }
      const out = await fetchOne(currentId, life.signal);
      if (out === "stop") return;
      if (out === "ok") {
        failures.current = 0;
        setFailing(false);
        timer = setTimeout(() => void run(), refreshSeconds * 1000);
      } else {
        timer = setTimeout(() => void run(), backoffSeconds(failures.current, retryAfter.current) * 1000);
      }
    };
    kick.current = () => {
      if (timer) clearTimeout(timer);
      void run();
    };
    // Just prefetched before the rotation switched here: do not fetch again right away.
    const age = Date.now() - (fetchedAt.current.get(currentId) ?? -Infinity);
    if (age < PREFETCH_LEAD_SECONDS * 2000) timer = setTimeout(() => void run(), refreshSeconds * 1000 - age);
    else void run();
    return () => {
      life.abort();
      if (timer) clearTimeout(timer);
      kick.current = null;
    };
  }, [token, currentId, refreshSeconds, fetchOne]);

  // The next dashboard: one prefetch shortly before the rotation. A failure is ignored (the switch refetches).
  useEffect(() => {
    if (token === null || currentId === null || nextId === null || nextId === currentId) return;
    const life = new AbortController();
    const delay = Math.max(0, rotateSeconds - PREFETCH_LEAD_SECONDS) * 1000;
    const timer = setTimeout(() => {
      if (isHidden()) return;
      if (token !== null) {
        void kioskApi
          .data(token, nextId, life.signal)
          .then((fresh) => {
            if (!life.signal.aborted) store(nextId, fresh);
          })
          .catch((err: unknown) => {
            if (err instanceof KioskLinkInvalidError && !life.signal.aborted) setInvalid(true);
          });
      }
    }, delay);
    return () => {
      life.abort();
      clearTimeout(timer);
    };
  }, [token, currentId, nextId, rotateSeconds, store]);

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 5000);
    return () => clearInterval(timer);
  }, []);

  const current = currentId === null ? undefined : byDashboard[currentId];
  const tooOld = current !== undefined && now - current.fetchedAt > STALE_INTERVALS * refreshSeconds * 1000;
  const refresh = useCallback(() => kick.current?.(), []);
  return { byDashboard, stale: failing || tooOld, invalid, refresh };
}
