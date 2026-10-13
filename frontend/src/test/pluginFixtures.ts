/**
 * Typed fixtures for frontend plugin tests (R-TS-REQUIRED-DEFAULTS): every
 * field the generated schema marks required — including the ones the backend
 * fills from defaults — so tests need no `as FrontendPlugin` casts.
 */
import type { Schemas } from "@tiqora/api-client";
import type { FrontendContribution, FrontendPlugin } from "@/lib/pluginsApi";

type Contribution<S extends FrontendContribution["slot"]> = Extract<FrontendContribution, { slot: S }>;

/**
 * The fixture plugin `org.example.hello` as `GET /api/v1/plugins/frontend`
 * returns it. `asset_base` follows an overridden id/version unless given.
 */
export function helloPlugin(overrides: Partial<FrontendPlugin> = {}): FrontendPlugin {
  const id = overrides.id ?? "org.example.hello";
  const version = overrides.version ?? "1.0.0";
  return {
    id,
    version,
    name: "Hallo",
    asset_base: `/api/v1/plugins/${id}/assets/${version}/`,
    entry: "web/index.js",
    styles: ["web/hello.css"],
    locales: ["de"],
    contributions: [],
    ...overrides,
  };
}

export function ticketPanel(key: string, order = 100, component = "HelloPanel"): Contribution<"ticket.sidebar"> {
  return { slot: "ticket.sidebar", key, component, title_key: `panel.${key}`, order };
}

export function navEntry(key: string, route: string, order = 100): Contribution<"agent.nav"> {
  return {
    slot: "agent.nav",
    key,
    label_key: `nav.${key}`,
    route,
    group: "plugins",
    icon: "puzzle",
    order,
    admin_only: false,
  };
}

export function routePage(key: string, path: string): Contribution<"agent.route"> {
  return { slot: "agent.route", key, path, component: "HelloPage", title_key: `page.${key}`, admin_only: false };
}

export function dashboardWidget(
  key: string,
  overrides: Partial<Contribution<"dashboard.widget">> = {},
): Contribution<"dashboard.widget"> {
  return {
    slot: "dashboard.widget",
    key,
    component: "HelloWidget",
    version: 1,
    title_key: `widget.${key}`,
    description_key: null,
    category: "other",
    icon: "puzzle",
    size: { default: [4, 3], min: [2, 2], max: [12, 8] },
    settings_schema: null,
    refresh_on: [],
    permission: null,
    ...overrides,
  };
}

type AdminPlugin = Schemas["PluginAdminOut"];
type AdminPluginList = Schemas["PluginAdminListOut"];

/** The install-state fields of `GET /api/v1/admin/plugins` with neutral values (runtime install on, nothing unusual). */
export function adminListFields(overrides: Partial<AdminPluginList> = {}): Omit<AdminPluginList, "safe_mode" | "plugins"> {
  return {
    runtime_install: true,
    runtime_install_reason: null,
    allow_unsigned: false,
    max_package_mb: 15,
    coordinated_restart: true,
    ...overrides,
  };
}
type AdminHook = AdminPlugin["hooks"][number];
type AdminRuntime = AdminPlugin["runtime"][number];

/** A sync hook row (`kind: "sync"`, `schedule` = the operation key) as `GET /api/v1/admin/plugins` returns it. */
export function syncHookRow(
  pluginId: string,
  name: string,
  operation: string,
  overrides: Partial<AdminHook> = {},
): AdminHook {
  return {
    key: `sync:${pluginId}:${name}`,
    kind: "sync",
    name,
    schedule: operation,
    tripped: false,
    consecutive_failures: 0,
    total_failures: 0,
    last_error: null,
    last_error_at: null,
    last_ok_at: null,
    last_run_at: null,
    ...overrides,
  };
}

/** The `ai-worker` runtime row of a plugin. */
export function aiWorkerRuntime(version = "1.0.0", overrides: Partial<AdminRuntime> = {}): AdminRuntime {
  return {
    role: "ai-worker",
    state: "active",
    version,
    error: null,
    boot_attempts: 1,
    updated_at: "2026-10-10T06:00:00Z",
    ...overrides,
  };
}
