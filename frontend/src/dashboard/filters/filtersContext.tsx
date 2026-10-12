import { createContext, useContext, useMemo, type ReactNode } from "react";
import type { DashboardFilters } from "@/lib/dashboardsApi";

export interface DashboardFiltersValue {
  filters: DashboardFilters;
  /** At least one queue or a time range is set. */
  active: boolean;
}

export const NO_DASHBOARD_FILTERS: DashboardFilters = Object.freeze({ queue_ids: [], time_range: null }) as DashboardFilters;

/** Whether `filters` narrow anything (the backend's `not is_empty()`). */
// eslint-disable-next-line react-refresh/only-export-components
export function filtersActive(filters: DashboardFilters | null | undefined): boolean {
  return !!filters && ((filters.queue_ids?.length ?? 0) > 0 || filters.time_range != null);
}

const FiltersContext = createContext<DashboardFiltersValue>({ filters: NO_DASHBOARD_FILTERS, active: false });

/** The open dashboard's filters, read by the pin glyph, the settings drawer and the preview data. */
export function DashboardFiltersProvider({ filters, children }: { filters: DashboardFilters | null | undefined; children: ReactNode }) {
  const value = useMemo<DashboardFiltersValue>(
    () => ({ filters: filters ?? NO_DASHBOARD_FILTERS, active: filtersActive(filters) }),
    [filters],
  );
  return <FiltersContext.Provider value={value}>{children}</FiltersContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useDashboardFilters(): DashboardFiltersValue {
  return useContext(FiltersContext);
}
