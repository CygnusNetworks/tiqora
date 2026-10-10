/**
 * Dashboard widget contract (P13), shared with the dashboards plan: core
 * widgets and plugin widgets register into the same extension point. The
 * prop/size/category types live once in `@tiqora/plugin-sdk` (R-WIDGET-GENERIC).
 */
import { createElement, useSyncExternalStore, type ComponentType } from "react";
import type { WidgetCategory, WidgetProps, WidgetSize } from "@tiqora/plugin-sdk";
import { createExtensionPoint, type ExtensionPoint } from "@/extensions/registry";
import type { FrontendContribution, FrontendPlugin } from "@/lib/pluginsApi";
import { PluginBoundary } from "./PluginBoundary";
import { PluginComponent } from "./PluginComponent";
import { pluginNamespace } from "./i18n";

export type { WidgetCategory, WidgetMode, WidgetProps, WidgetSize } from "@tiqora/plugin-sdk";

export interface WidgetDefinition<S = Record<string, unknown>> {
  /** core: "core.<name>"; plugin: "<plugin_id>.<key>" */
  key: string;
  /** "core" or the plugin id */
  owner: string;
  version: number;
  /** Resolved in `ns` (plugin) or the default namespace (core). */
  titleKey: string;
  descriptionKey?: string;
  /** "plugin.<id>" for plugins */
  ns?: string;
  category: WidgetCategory;
  icon: string;
  size: WidgetSize;
  /** JSON schema, SchemaForm subset */
  settingsSchema: Record<string, unknown> | null;
  /** Backend provider key (the dashboards plan adds core.dashboard.providers);
   * null for plugin widgets in phase 1 (they call their own routes). */
  dataEndpoint: string | null;
  /** SSE event types */
  refreshOn: string[];
  /** Reserved; phase 1 ignores it (plugin widgets are shown to every agent who
   * sees the plugin). The dashboards plan defines its semantics. */
  permission: string | null;
  /** Plugin widgets: lazy, wrapped in PluginBoundary by the host. */
  component: ComponentType<WidgetProps<S>>;
  migrate?: (old: unknown, fromVersion: number) => S;
}

// Items with any settings type: a WidgetDefinition<{ city: string }> is not
// assignable to WidgetDefinition<Record<string, unknown>> under strictFunctionTypes.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const point = createExtensionPoint<WidgetDefinition<any>>("core.dashboard.widgets", (w) => w.key);

/** The point itself, except that every change wakes useDashboardWidgetDefinitions
 * — also for core widgets the dashboards plan registers later. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const DASHBOARD_WIDGETS: ExtensionPoint<WidgetDefinition<any>> = {
  ...point,
  register(item, owner) {
    point.register(item, owner);
    notify();
  },
  clearForTests() {
    point.clearForTests();
    notify();
  },
};

/** Typed widget definition → an item the point accepts, without casts at the call site. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function defineWidget<S>(def: WidgetDefinition<S>): WidgetDefinition<any> {
  return def;
}

// --- change notification for useDashboardWidgetDefinitions ---------------

const listeners = new Set<() => void>();
let snapshot: WidgetDefinition[] = [];

function notify(): void {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** The point's current items; the same array as long as nothing changed. */
function getSnapshot(): WidgetDefinition[] {
  const items = DASHBOARD_WIDGETS.list();
  if (items.length !== snapshot.length || items.some((item, i) => item !== snapshot[i])) {
    snapshot = items;
  }
  return snapshot;
}

type WidgetContribution = Extract<FrontendContribution, { slot: "dashboard.widget" }>;

function pluginWidget(plugin: FrontendPlugin, c: WidgetContribution): WidgetDefinition {
  function PluginWidget(props: WidgetProps) {
    return createElement(PluginBoundary, {
      plugin,
      children: createElement(PluginComponent, { plugin, name: c.component, props }),
    });
  }
  PluginWidget.displayName = `PluginWidget(${plugin.id}.${c.key})`;
  return {
    key: `${plugin.id}.${c.key}`,
    owner: plugin.id,
    version: c.version,
    titleKey: c.title_key,
    descriptionKey: c.description_key ?? undefined,
    ns: pluginNamespace(plugin.id),
    category: c.category,
    icon: c.icon,
    size: { default: [...c.size.default], min: [...c.size.min], max: [...c.size.max] },
    settingsSchema: c.settings_schema ?? null,
    dataEndpoint: null,
    refreshOn: [...c.refresh_on],
    permission: c.permission ?? null,
    component: PluginWidget,
  };
}

/**
 * Register the `dashboard.widget` contributions of these plugins (owner =
 * plugin id). Idempotent: a key the same plugin already registered is
 * skipped; a key owned by someone else is left alone with a warning.
 * Phase 1 keeps the first registration for the page's lifetime (the list is
 * fetched once; the point has no replace/unregister yet).
 */
export function registerPluginWidgets(plugins: FrontendPlugin[]): void {
  for (const plugin of plugins) {
    for (const c of plugin.contributions) {
      if (c.slot !== "dashboard.widget") continue;
      const key = `${plugin.id}.${c.key}`;
      const owner = DASHBOARD_WIDGETS.owner(key);
      if (owner === plugin.id) continue;
      if (owner !== undefined) {
        console.warn(`[plugin ${plugin.id}] widget "${key}" is already registered by ${owner}`);
        continue;
      }
      DASHBOARD_WIDGETS.register(pluginWidget(plugin, c), plugin.id);
    }
  }
}

/** Every registered widget (core and plugins); re-renders when plugins finished loading. */
export function useDashboardWidgetDefinitions(): WidgetDefinition[] {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}
