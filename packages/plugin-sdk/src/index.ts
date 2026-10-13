/**
 * @tiqora/plugin-sdk — the only frontend API a Tiqora plugin may use.
 *
 * Types live here. The runtime values are only *declared*: a plugin bundle
 * built with `@tiqora/plugin-sdk/vite` imports them from
 * `/plugin-runtime/sdk.js`, which the Tiqora page serves (implementation:
 * `frontend/src/plugins/sdkRuntime.tsx`). Host code imports from this module
 * with `import type` only.
 *
 * Every change to this surface bumps the version here and `SDK_VERSION` in
 * the Python `tiqora_sdk` together (0.x minors may break).
 */
import type { ComponentType, ReactNode } from "react";

export interface PluginUser {
  id: number;
  login: string;
  isAdmin: boolean;
}

export interface PluginContextValue {
  pluginId: string;
  version: string;
  locale: string;
  user: PluginUser | null;
}

export interface PluginApiRequestOptions {
  query?: Record<string, string | number | boolean | undefined>;
  signal?: AbortSignal;
}

/** Paths are relative to `/api/plugins/<pluginId>/`. */
export interface PluginApi {
  get<T>(path: string, opts?: PluginApiRequestOptions): Promise<T>;
  post<T>(path: string, body?: unknown, opts?: PluginApiRequestOptions): Promise<T>;
  put<T>(path: string, body?: unknown, opts?: PluginApiRequestOptions): Promise<T>;
  delete<T>(path: string, opts?: PluginApiRequestOptions): Promise<T>;
}

/** Props of an `agent.route` component. */
export interface RoutePageProps {
  path: string;
  subPath: string;
}

/** Props of a `ticket.sidebar` component. */
export interface TicketPanelProps {
  ticketId: number;
}

// --- Dashboard widgets (contract shared with the dashboards plan) ---------

export type WidgetCategory = "tickets" | "reports" | "team" | "ai" | "itsm" | "other";
export type WidgetMode = "view" | "edit" | "preview" | "wallboard";

/** `[w, h]` in 12-column grid cells. */
export interface WidgetSize {
  default: [number, number];
  min: [number, number];
  max: [number, number];
}

export interface WidgetProps<S = Record<string, unknown>> {
  /** Stable id of the placed widget; "preview" in the gallery. */
  instanceId: string;
  /** Defaults from settingsSchema applied. */
  settings: S;
  /** Cells currently occupied. */
  size: { w: number; h: number };
  /** Dashboard-wide filters ({} until dashboards phase 2). */
  filters: Readonly<Record<string, unknown>>;
  mode: WidgetMode;
}

// --- Plugin module and runtime surface ------------------------------------

export interface PluginDefinition {
  // Any props: each component has its own (RoutePageProps, TicketPanelProps,
  // WidgetProps, …); a narrower type fails under strictFunctionTypes.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  components: Record<string, ComponentType<any>>;
}

export interface SdkRuntime {
  SDK_VERSION: "0.2.0";
  definePlugin(def: PluginDefinition): PluginDefinition;
  usePluginContext(): PluginContextValue;
  usePluginApi(): PluginApi;
  usePluginTranslation(): { t: (key: string, options?: Record<string, unknown>) => string };
  Button: ComponentType<{
    variant?: "primary" | "secondary" | "ghost" | "danger";
    size?: "sm" | "md";
    type?: "button" | "submit";
    disabled?: boolean;
    onClick?: () => void;
    children: ReactNode;
  }>;
  Badge: ComponentType<{
    tone?: "default" | "accent" | "warn" | "danger" | "success" | "muted";
    children: ReactNode;
  }>;
  Spinner: ComponentType<Record<string, never>>;
  Switch: ComponentType<{
    checked: boolean;
    onChange: (checked: boolean) => void;
    disabled?: boolean;
    label: string;
  }>;
  Dialog: ComponentType<{
    open: boolean;
    onClose: () => void;
    title: string;
    children: ReactNode;
    footer?: ReactNode;
  }>;
  Panel: ComponentType<{ title: string; actions?: ReactNode; children: ReactNode }>;
  EmptyState: ComponentType<{ title: string; hint?: string }>;
}

// Runtime values: provided by the Tiqora page at /plugin-runtime/sdk.js.
export declare const SDK_VERSION: SdkRuntime["SDK_VERSION"];
export declare const definePlugin: SdkRuntime["definePlugin"];
export declare const usePluginContext: SdkRuntime["usePluginContext"];
export declare const usePluginApi: SdkRuntime["usePluginApi"];
export declare const usePluginTranslation: SdkRuntime["usePluginTranslation"];
export declare const Button: SdkRuntime["Button"];
export declare const Badge: SdkRuntime["Badge"];
export declare const Spinner: SdkRuntime["Spinner"];
export declare const Switch: SdkRuntime["Switch"];
export declare const Dialog: SdkRuntime["Dialog"];
export declare const Panel: SdkRuntime["Panel"];
export declare const EmptyState: SdkRuntime["EmptyState"];
