import type { TFunction } from "i18next";
import type { PluginAdmin } from "@/lib/pluginsApi";

export type CapabilityLine = PluginAdmin["capabilities"][number];

const POINT_KEYS: Record<string, string> = {
  "core.events.subscribers": "events",
  "core.http.routes": "routes",
  "core.http.public_routes": "public_routes",
  "core.mcp.tools": "mcp_tools",
  "core.worker.scheduled_tasks": "scheduled_tasks",
  "core.reporting.builtin_reports": "builtin_reports",
  "core.reporting.sources": "report_sources",
  "core.events.sync_hooks": "sync_hooks",
  "core.process.transition_actions": "transition_actions",
  "core.process.condition_modules": "condition_modules",
  "core.templates.placeholders": "placeholders",
  "core.tickets.number_generators": "number_generators",
  "core.dynamic_fields.types": "df_types",
  "core.mail.inbound_filters": "inbound_mail_filters",
  "core.mail.outbound_filters": "outbound_mail_filters",
  "core.notifications.transports": "notification_transports",
  "core.channels.drivers": "channel_drivers",
  "core.auth.password_providers": "password_providers",
  "core.customers.sources": "customer_sources",
  "core.gi.operations": "gi_operations",
  "core.gi.invokers": "gi_invokers",
  "core.gi.mappings": "gi_mappings",
};

const SLOT_KEYS: Record<string, string> = {
  "agent.nav": "nav",
  "agent.route": "route",
  "ticket.sidebar": "ticket_sidebar",
  "dashboard.widget": "widget",
};

/** One capability in plain words (what the extension declares it does). */
export function capabilityText(t: TFunction, cap: CapabilityLine): string {
  if (cap.kind === "table") return t("admin.plugins.capabilities.table", { name: cap.value });
  if (cap.kind === "outbound_host") return t("admin.plugins.capabilities.host", { host: cap.value });
  if (cap.kind === "extension_point") {
    const key = POINT_KEYS[cap.value];
    return key
      ? t(`admin.plugins.capabilities.point.${key}`)
      : t("admin.plugins.capabilities.point.other", { value: cap.value });
  }
  const key = SLOT_KEYS[cap.value];
  return key
    ? t(`admin.plugins.capabilities.slot.${key}`)
    : t("admin.plugins.capabilities.slot.other", { value: cap.value });
}

