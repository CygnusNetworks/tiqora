/**
 * Widget data reaches components through a context, not through `WidgetProps` (the shared
 * contract has no data field). The host wraps each placed widget in `WidgetDataProvider` (fed by
 * the batched `/dashboards/{id}/data` result); the settings drawer feeds it from the preview route.
 */
import { createContext, useContext, type ReactNode } from "react";

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
