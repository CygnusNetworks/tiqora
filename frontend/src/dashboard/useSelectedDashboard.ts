import { useCallback, useEffect, useState } from "react";
import { useOptionalAuth } from "@/auth/AuthContext";
import type { DashboardTab } from "@/lib/dashboardsApi";

/** localStorage key of the dashboard the agent looked at last (one per browser). */
export const LAST_DASHBOARD_KEY = "tiqora.dashboard.last";

function readLast(): number | null {
  try {
    const raw = window.localStorage.getItem(LAST_DASHBOARD_KEY);
    const id = raw === null ? NaN : Number(raw);
    return Number.isSafeInteger(id) && id > 0 ? id : null;
  } catch {
    return null;
  }
}

function writeLast(id: number): void {
  try {
    window.localStorage.setItem(LAST_DASHBOARD_KEY, String(id));
  } catch {
    // blocked or full storage: the choice is simply not remembered
  }
}

/**
 * Which dashboard the page shows. Preference: the tab the agent picked on this page -> `?d=` (a
 * visible tab, or for admins any id, e.g. a role template opened from the admin page) ->
 * `tiqora.dashboard.last` -> the first tab. `reject(id)` marks an id that does not exist (404), so
 * the choice falls back to the next candidate. Storage errors are swallowed.
 */
export function useSelectedDashboard(
  tabs: DashboardTab[] | undefined,
  search: { d?: number },
): { selectedId: number | null; select: (id: number) => void; reject: (id: number) => void } {
  const isAdmin = useOptionalAuth()?.user?.is_admin === true;
  const [picked, setPicked] = useState<number | null>(null);
  const [rejected, setRejected] = useState<ReadonlySet<number>>(() => new Set());
  const d = search.d;

  // A new `?d=` (link from elsewhere while the page is open) wins over an earlier pick.
  useEffect(() => {
    setPicked(null);
  }, [d]);

  const select = useCallback((id: number) => {
    setPicked(id);
    writeLast(id);
  }, []);
  const reject = useCallback((id: number) => {
    setRejected((prev) => (prev.has(id) ? prev : new Set(prev).add(id)));
  }, []);

  if (!tabs) return { selectedId: null, select, reject };
  const isTab = (id: number | null | undefined): id is number =>
    id != null && tabs.some((tab) => tab.id === id);
  let selectedId: number | null = null;
  if (isTab(picked)) selectedId = picked;
  else if (isTab(d) || (d != null && isAdmin && !rejected.has(d))) selectedId = d;
  else {
    const last = readLast();
    selectedId = isTab(last) ? last : (tabs[0]?.id ?? null);
  }
  return { selectedId, select, reject };
}
