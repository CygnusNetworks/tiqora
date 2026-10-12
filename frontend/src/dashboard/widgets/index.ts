/**
 * The ten phase-1 core widgets (D9 table) and their registration into the shared
 * `DASHBOARD_WIDGETS` point. Settings schemas mirror `backend/src/tiqora/dashboard/settings_models.py`
 * (same fields, bounds and defaults); `settingsWithDefaults` fills them on the client.
 */
import { DASHBOARD_WIDGETS, type WidgetDefinition } from "@/plugins/dashboardWidgets";
import { COLUMN_KEYS, TICKET_PRESETS, defineCoreWidget } from "../definitions";
import { AiStatusWidget, type AiStatusSettings } from "./AiStatusWidget";
import { BacklogAgingWidget, type BacklogAgingSettings } from "./BacklogAgingWidget";
import { CalendarAgendaWidget, type CalendarAgendaSettings } from "./CalendarAgendaWidget";
import { ChatQueueWidget, type ChatQueueSettings } from "./ChatQueueWidget";
import { CounterWidget, type CounterSettings } from "./CounterWidget";
import { EscalationsWidget, type EscalationsSettings } from "./EscalationsWidget";
import { HeatmapWidget, type HeatmapSettings } from "./HeatmapWidget";
import { LinksWidget, type LinksSettings } from "./LinksWidget";
import { NotesWidget, type NotesSettings } from "./NotesWidget";
import { QueueOverviewWidget, type QueueOverviewSettings } from "./QueueOverviewWidget";
import { RecentViewsWidget, type RecentViewsSettings } from "./RecentViewsWidget";
import { RemindersWidget, type RemindersSettings } from "./RemindersWidget";
import { ReportWidget, type ReportWidgetSettings } from "./ReportWidget";
import { SlaGaugeWidget, type SlaGaugeSettings } from "./SlaGaugeWidget";
import { TeamPresenceWidget, type TeamPresenceSettings } from "./TeamPresenceWidget";
import { TicketListWidget, type TicketListSettings } from "./TicketListWidget";
import { TIME_RANGE_PRESETS } from "../filters/timeRanges";
import { DEFAULT_SOURCE } from "./presets";

const int = (minimum: number, maximum: number, dflt: number) => ({
  type: "integer",
  minimum,
  maximum,
  default: dflt,
});
const object = (properties: Record<string, unknown>) => ({ type: "object", properties });
const ticketSource = {
  type: "object",
  properties: {
    kind: { type: "string", enum: ["preset", "saved_search", "filter"] },
    preset: { type: "string", enum: [...TICKET_PRESETS] },
    id: { type: "integer", minimum: 1 },
    filter: { type: "object" },
  },
  default: DEFAULT_SOURCE,
};
const texts = (name: string) => ({
  title: `dashboards.widgets.${name}.title`,
  description: `dashboards.widgets.${name}.description`,
});
const TICKET_EVENTS = ["ticket_changed", "ticket_new_in_queue"];

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const CORE_WIDGETS: WidgetDefinition<any>[] = [
  defineCoreWidget<TicketListSettings>({
    key: "core.ticket-list",
    version: 1,
    ...texts("ticketList"),
    category: "tickets",
    icon: "list",
    size: { default: [6, 5], min: [4, 3], max: [12, 10] },
    settings: object({
      source: ticketSource,
      columns: {
        type: "array",
        items: { type: "string", enum: [...COLUMN_KEYS] },
        minItems: 1,
        maxItems: 8,
        default: ["tn", "title", "customer", "state", "due"],
      },
      sort: { type: "string", enum: ["age", "changed", "priority", "deadline"], default: "age" },
      order: { type: "string", enum: ["asc", "desc"], default: "desc" },
      limit: int(1, 50, 8),
      density: { type: "string", enum: ["compact", "comfortable"], default: "comfortable" },
    }),
    data: { endpoint: "core.ticket-list" },
    refreshOn: TICKET_EVENTS,
    component: TicketListWidget,
  }),
  defineCoreWidget<CounterSettings>({
    key: "core.counter",
    version: 1,
    ...texts("counter"),
    category: "tickets",
    icon: "counter",
    size: { default: [3, 2], min: [2, 2], max: [4, 3] },
    settings: object({
      source: ticketSource,
      tone: { type: "string", enum: ["neutral", "danger", "ok"], default: "neutral" },
    }),
    data: { endpoint: "core.counter" },
    refreshOn: TICKET_EVENTS,
    component: CounterWidget,
  }),
  defineCoreWidget<QueueOverviewSettings>({
    key: "core.queue-overview",
    version: 1,
    ...texts("queueOverview"),
    category: "tickets",
    icon: "queue",
    size: { default: [12, 3], min: [4, 2], max: [12, 6] },
    settings: object({ limit: int(1, 12, 8), only_with_work: { type: "boolean", default: true } }),
    data: { endpoint: "core.queue-overview" },
    refreshOn: ["ticket_changed"],
    component: QueueOverviewWidget,
  }),
  defineCoreWidget<EscalationsSettings>({
    key: "core.escalations",
    version: 1,
    ...texts("escalations"),
    category: "tickets",
    icon: "alert",
    size: { default: [12, 3], min: [4, 2], max: [12, 8] },
    settings: object({ limit: int(1, 20, 8), within_hours: int(0, 72, 8) }),
    data: { endpoint: "core.escalations" },
    refreshOn: ["ticket_changed", "interval:60"],
    component: EscalationsWidget,
  }),
  defineCoreWidget<RemindersSettings>({
    key: "core.reminders",
    version: 1,
    ...texts("reminders"),
    category: "other",
    icon: "clock",
    size: { default: [6, 5], min: [3, 3], max: [12, 8] },
    settings: object({ window_hours: int(1, 168, 24), limit: int(1, 20, 6) }),
    data: { endpoint: "core.reminders" },
    refreshOn: ["ticket_changed", "interval:300"],
    component: RemindersWidget,
  }),
  defineCoreWidget<RecentViewsSettings>({
    key: "core.recent-views",
    version: 1,
    ...texts("recentViews"),
    category: "other",
    icon: "history",
    size: { default: [12, 2], min: [3, 2], max: [12, 4] },
    settings: object({ limit: int(1, 12, 8) }),
    data: null,
    refreshOn: [],
    component: RecentViewsWidget,
  }),
  defineCoreWidget<CalendarAgendaSettings>({
    key: "core.calendar-agenda",
    version: 1,
    ...texts("calendarAgenda"),
    category: "other",
    icon: "calendar",
    size: { default: [4, 3], min: [3, 2], max: [8, 8] },
    settings: object({ days: int(1, 14, 1), limit: int(1, 20, 6) }),
    data: { endpoint: "core.calendar-agenda" },
    refreshOn: ["interval:300"],
    component: CalendarAgendaWidget,
  }),
  defineCoreWidget<TeamPresenceSettings>({
    key: "core.team-presence",
    version: 1,
    ...texts("teamPresence"),
    category: "team",
    icon: "users",
    size: { default: [4, 3], min: [3, 2], max: [6, 8] },
    settings: object({ limit: int(1, 30, 8) }),
    data: { endpoint: "core.team-presence" },
    refreshOn: ["interval:30"],
    component: TeamPresenceWidget,
  }),
  defineCoreWidget<NotesSettings>({
    key: "core.notes",
    version: 1,
    ...texts("notes"),
    category: "team",
    icon: "note",
    size: { default: [4, 3], min: [3, 2], max: [12, 10] },
    settings: object({ markdown: { type: "string", maxLength: 5000, default: "" } }),
    data: null,
    refreshOn: [],
    component: NotesWidget,
  }),
  defineCoreWidget<LinksSettings>({
    key: "core.links",
    version: 1,
    ...texts("links"),
    category: "team",
    icon: "link",
    size: { default: [4, 2], min: [2, 2], max: [12, 6] },
    settings: object({
      items: {
        type: "array",
        maxItems: 12,
        items: {
          type: "object",
          properties: { label: { type: "string", maxLength: 80 }, url: { type: "string", maxLength: 500 } },
        },
        default: [],
      },
    }),
    data: null,
    refreshOn: [],
    component: LinksWidget,
  }),
  defineCoreWidget<ReportWidgetSettings>({
    key: "core.report",
    version: 1,
    ...texts("report"),
    category: "reports",
    icon: "chart",
    size: { default: [6, 5], min: [4, 3], max: [12, 10] },
    settings: object({
      // no default: a report has to be chosen (the settings drawer opens on add)
      report_key: { type: "string", maxLength: 80 },
      time_range: { type: "string", enum: [...TIME_RANGE_PRESETS] },
      parameters: { type: "object", default: {} },
      dimension_choices: { type: "object", default: {} },
      display: { type: "string", enum: ["auto", "table"], default: "auto" },
      max_rows: int(1, 50, 10),
    }),
    data: { endpoint: "core.report" },
    refreshOn: ["interval:300"],
    permission: "stats",
    component: ReportWidget,
  }),
  defineCoreWidget<SlaGaugeSettings>({
    key: "core.sla-gauge",
    version: 1,
    ...texts("slaGauge"),
    category: "reports",
    icon: "gauge",
    size: { default: [3, 3], min: [3, 3], max: [6, 5] },
    settings: object({
      time_range: { type: "string", enum: [...TIME_RANGE_PRESETS], default: "30d" },
      target_percent: int(50, 100, 90),
    }),
    data: { endpoint: "core.sla-gauge" },
    refreshOn: ["interval:300"],
    permission: "stats",
    component: SlaGaugeWidget,
  }),
  defineCoreWidget<BacklogAgingSettings>({
    key: "core.backlog-aging",
    version: 1,
    ...texts("backlogAging"),
    category: "reports",
    icon: "layers",
    size: { default: [6, 5], min: [4, 4], max: [12, 8] },
    settings: object({ max_rows: int(1, 15, 8) }),
    data: { endpoint: "core.backlog-aging" },
    refreshOn: ["interval:300"],
    permission: "stats",
    component: BacklogAgingWidget,
  }),
  defineCoreWidget<HeatmapSettings>({
    key: "core.heatmap",
    version: 1,
    ...texts("heatmap"),
    category: "reports",
    icon: "grid",
    size: { default: [6, 4], min: [6, 4], max: [12, 6] },
    settings: object({ time_range: { type: "string", enum: ["30d", "90d"], default: "90d" } }),
    data: { endpoint: "core.heatmap" },
    refreshOn: ["interval:900"],
    permission: "stats",
    component: HeatmapWidget,
  }),
  defineCoreWidget<ChatQueueSettings>({
    key: "core.chat-queue",
    version: 1,
    ...texts("chatQueue"),
    category: "team",
    icon: "chat",
    size: { default: [4, 3], min: [3, 2], max: [6, 6] },
    settings: object({ limit: int(1, 10, 5) }),
    data: { endpoint: "core.chat-queue" },
    refreshOn: ["chat_waiting", "chat_assigned", "chat_ended", "interval:30"],
    component: ChatQueueWidget,
  }),
  defineCoreWidget<AiStatusSettings>({
    key: "core.ai-status",
    version: 1,
    ...texts("aiStatus"),
    category: "ai",
    icon: "spark",
    size: { default: [4, 3], min: [3, 3], max: [6, 6] },
    settings: object({}),
    data: { endpoint: "core.ai-status" },
    refreshOn: ["interval:300"],
    component: AiStatusWidget,
  }),
];

/** Registers the core widgets once (owner "core"); keys already registered are left alone. */
export function registerCoreWidgets(): void {
  for (const def of CORE_WIDGETS) {
    if (DASHBOARD_WIDGETS.get(def.key)) continue;
    DASHBOARD_WIDGETS.register(def, "core");
  }
}
