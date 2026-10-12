import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import i18n from "@/i18n";
import { api, type OccurrenceOut, type OnlineAgentOut, type TicketListItem } from "@/lib/api";
import { setDisplayTimeZone } from "@/lib/timeZone";
import { DASHBOARD_WIDGETS, type WidgetMode } from "@/plugins/dashboardWidgets";
import { CORE_WIDGET_KEYS, settingsWithDefaults } from "../definitions";
import { WidgetDataProvider, type WidgetDataValue } from "../data";
import { WidgetChromeProvider, type WidgetChrome } from "../chrome/WidgetFrame";
import { KioskProvider } from "../wallboard/kioskContext";
import { registerCoreWidgets } from "./index";

vi.mock("@/auth/AuthContext", async () => {
  const actual = await vi.importActual<typeof import("@/auth/AuthContext")>("@/auth/AuthContext");
  return { ...actual, useOptionalAuth: () => ({ user: { id: 5 } }) };
});

// ---------------------------------------------------------------- fixtures and helpers

type WidgetDataState = Omit<WidgetDataValue, "refetch">;

/** The core widgets that read provider data. */
const DATA_WIDGET_KEYS = [
  "core.ticket-list",
  "core.counter",
  "core.escalations",
  "core.reminders",
  "core.calendar-agenda",
  "core.team-presence",
  "core.queue-overview",
  "core.chat-queue",
  "core.ai-status",
];

function ticket(id: number, over: Partial<TicketListItem> = {}): TicketListItem {
  return {
    id,
    tn: `20261009120000${String(id).padStart(2, "0")}`,
    title: `Ticket ${id}`,
    queue_id: 2,
    queue_name: "Support",
    state_id: 4,
    state: "open",
    state_type: "open",
    priority_id: 3,
    priority: "3 normal",
    lock_id: 1,
    owner_id: 5,
    owner_name: "Anna Agent",
    create_time: "2026-10-09T08:00:00Z",
    change_time: "2026-10-09T09:00:00Z",
    escalation_time: 0,
    escalation_response_time: 0,
    escalation_update_time: 0,
    escalation_solution_time: 0,
    until_time: 0,
    attachment_count: 0,
    channel: "email",
    has_ai_summary: false,
    ai_escalated: false,
    ai_paused: false,
    archive_flag: 0,
    customer_id: "beispiel-gmbh",
    customer_email: "mia.berg@example.org",
    ...over,
  };
}

const rows = (n: number) => Array.from({ length: n }, (_, i) => ticket(i + 1));
const preset = (name: string) => ({ source: { kind: "preset", preset: name } });

function occ(over: Partial<OccurrenceOut> = {}): OccurrenceOut {
  return {
    appointment_id: 1,
    calendar_id: 1,
    title: "Termin",
    description: null,
    location: null,
    start_time: "2026-10-10T08:00:00",
    end_time: "2026-10-10T09:00:00",
    all_day: false,
    is_recurring: false,
    ...over,
  };
}

function agent(fullName: string, id = fullName.length): OnlineAgentOut {
  return { id, login: fullName.toLowerCase().replace(/\s+/g, "."), full_name: fullName, avatar_url: null };
}

let queryClient: QueryClient;

/**
 * Mounts the registered core widget `key` the way the host does: settings with defaults, the
 * data context, the host chrome context, a memory router (with the link targets) and i18n.
 */
async function renderWidget(
  key: string,
  settings: Record<string, unknown>,
  state: WidgetDataState,
  mode: WidgetMode = "view",
  chrome: WidgetChrome = {},
  refetch: () => void = () => {},
  opts: { size?: { w: number; h: number }; kiosk?: boolean } = {},
) {
  const def = DASHBOARD_WIDGETS.get(key);
  if (!def) throw new Error(`widget ${key} is not registered`);
  const Component = def.component;
  const inner = (
    <WidgetChromeProvider value={chrome}>
      <WidgetDataProvider value={{ ...state, refetch }}>
        <Component
          instanceId="w-test"
          settings={settingsWithDefaults(def, settings, def.version)}
          size={opts.size ?? { w: def.size.default[0], h: def.size.default[1] }}
          filters={{}}
          mode={mode}
        />
      </WidgetDataProvider>
    </WidgetChromeProvider>
  );
  const ui = opts.kiosk ? <KioskProvider>{inner}</KioskProvider> : inner;
  const rootRoute = createRootRoute({ component: () => ui });
  const children = ["/agent/tickets/drill", "/agent/tickets/$ticketId", "/agent/queues", "/agent/calendar", "/agent/searches/$searchId"].map((path) =>
    createRoute({ getParentRoute: () => rootRoute, path }),
  );
  const router = createRouter({
    routeTree: rootRoute.addChildren(children),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  await router.load();
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
        <RouterProvider router={router as any} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

beforeAll(async () => {
  await i18n.changeLanguage("de");
  // The memory router restores scroll on navigation; jsdom has no window.scrollTo.
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
});

beforeEach(() => {
  DASHBOARD_WIDGETS.clearForTests();
  registerCoreWidgets();
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  localStorage.clear();
  setDisplayTimeZone("Europe/Berlin");
});

afterEach(() => {
  vi.useRealTimers();
  DASHBOARD_WIDGETS.clearForTests();
});

// ---------------------------------------------------------------- registry wiring

const REPORT_WIDGET_KEYS = ["core.report", "core.sla-gauge", "core.backlog-aging", "core.heatmap"];
const TEAM_WIDGET_KEYS = ["core.chat-queue", "core.ai-status"];

describe("registerCoreWidgets", () => {
  it("registers exactly the ten phase-1 core keys and the phase-2 widgets once, owned by core, with the D9 sizes, and is idempotent", () => {
    DASHBOARD_WIDGETS.clearForTests();
    registerCoreWidgets();
    registerCoreWidgets();
    expect(DASHBOARD_WIDGETS.list().map((w) => w.key).sort()).toEqual([...CORE_WIDGET_KEYS, ...REPORT_WIDGET_KEYS, ...TEAM_WIDGET_KEYS].sort());
    expect(CORE_WIDGET_KEYS).toHaveLength(10);
    expect(DASHBOARD_WIDGETS.get("core.counter")!.size).toEqual({ default: [3, 2], min: [2, 2], max: [4, 3] });
    expect(DASHBOARD_WIDGETS.get("core.ticket-list")!.size).toEqual({ default: [6, 5], min: [4, 3], max: [12, 10] });
    expect(DASHBOARD_WIDGETS.get("core.links")!.size).toEqual({ default: [4, 2], min: [2, 2], max: [12, 6] });
    for (const key of CORE_WIDGET_KEYS) expect(DASHBOARD_WIDGETS.owner(key)).toBe("core");
  });

  it("wires the D9 data endpoints, refresh triggers and categories", () => {
    const get = (k: string) => DASHBOARD_WIDGETS.get(k)!;
    expect(get("core.recent-views").dataEndpoint).toBeNull();
    expect(get("core.notes").dataEndpoint).toBeNull();
    expect(get("core.links").dataEndpoint).toBeNull();
    for (const k of DATA_WIDGET_KEYS) expect(get(k).dataEndpoint).toBe(k);
    expect(get("core.escalations").refreshOn).toEqual(["ticket_changed", "interval:60"]);
    // the server-backed queue overview also counts brand-new tickets (E10: TICKET_EVENTS)
    expect(get("core.queue-overview").refreshOn).toEqual(["ticket_changed", "ticket_new_in_queue"]);
    expect(get("core.team-presence").refreshOn).toEqual(["interval:30"]);
    expect(get("core.chat-queue").refreshOn).toEqual(["chat_waiting", "chat_assigned", "chat_ended", "interval:30"]);
    expect(get("core.chat-queue").category).toBe("team");
    expect(get("core.ai-status").category).toBe("ai");
    expect(get("core.chat-queue").size).toEqual({ default: [4, 3], min: [3, 2], max: [6, 6] });
    expect(get("core.ai-status").size).toEqual({ default: [4, 3], min: [3, 3], max: [6, 6] });
    expect(get("core.reminders").category).toBe("other");
    expect(get("core.notes").category).toBe("team");
    for (const k of [...CORE_WIDGET_KEYS, ...TEAM_WIDGET_KEYS]) expect(get(k).permission).toBeNull();
  });

  it("keeps a key someone else registered first instead of throwing", () => {
    const counter = { ...DASHBOARD_WIDGETS.get("core.counter")!, owner: "org.example" };
    DASHBOARD_WIDGETS.clearForTests();
    DASHBOARD_WIDGETS.register(counter, "org.example");
    expect(() => registerCoreWidgets()).not.toThrow();
    expect(DASHBOARD_WIDGETS.owner("core.counter")).toBe("org.example");
    expect(DASHBOARD_WIDGETS.list()).toHaveLength(10 + REPORT_WIDGET_KEYS.length + TEAM_WIDGET_KEYS.length);
  });
});

// ---------------------------------------------------------------- shared states

describe.each(DATA_WIDGET_KEYS)("%s data states", (key) => {
  it("renders skeleton for loading and pending, error with retry, forbidden without retry", async () => {
    const show = async (st: WidgetDataState, refetch?: () => void) => {
      cleanup();
      return renderWidget(key, {}, st, "view", {}, refetch);
    };
    await show({ status: "loading" });
    expect(screen.getByTestId("widget-skeleton")).toBeVisible();
    await show({ status: "pending" });
    expect(screen.getByTestId("widget-skeleton")).toBeVisible();
    const refetch = vi.fn();
    await show({ status: "error", error: "Boom" }, refetch);
    expect(screen.getByText("Boom")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Erneut versuchen" }));
    expect(refetch).toHaveBeenCalledTimes(1);
    await show({ status: "error" });
    expect(screen.getByText("Diese Daten konnten nicht geladen werden.")).toBeVisible();
    await show({ status: "forbidden", error: "Nein." });
    expect(screen.getByText("Nein.")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Erneut versuchen" })).toBeNull();
    await show({ status: "forbidden" });
    expect(screen.getByText("Dafür fehlt Ihnen die Berechtigung.")).toBeVisible();
  });
});

// ---------------------------------------------------------------- counter

describe("core.counter", () => {
  it("shows label, value, delta and sparkline; fewer-is-better counters colour a drop green", async () => {
    const { container } = await renderWidget(
      "core.counter",
      { ...preset("escalated"), tone: "danger" },
      { status: "ok", data: { value: 2, delta: -1, spark: [3, 3, 2] } },
    );
    expect(screen.getByText("Eskaliert")).toBeVisible();
    expect(screen.getByText("2")).toHaveClass("text-danger");
    expect(screen.getByText("−1 seit dem letzten Stand")).toHaveClass("text-green");
    expect(container.querySelector("polyline")).toHaveClass("stroke-danger");
  });

  it("on a narrow tile the label stays and the delta text gives way (truncates, full text as title)", async () => {
    await renderWidget("core.counter", preset("unowned_new"), { status: "ok", data: { value: 5, delta: 2, spark: [3, 5] } });
    const label = screen.getByRole("heading", { name: "Zum Übernehmen" });
    expect(label).toHaveClass("shrink-0", "max-w-full", "truncate");
    expect(label).not.toHaveClass("flex-1");
    const delta = screen.getByText("+2 seit dem letzten Stand");
    expect(delta).toHaveClass("min-w-0", "truncate");
    expect(delta).not.toHaveClass("shrink-0");
    expect(delta).toHaveAttribute("title", "+2 seit dem letzten Stand");
  });

  it("keeps a rise of a fewer-is-better counter and every change of the others muted", async () => {
    await renderWidget("core.counter", preset("unowned_new"), { status: "ok", data: { value: 5, delta: 2, spark: [3, 5] } });
    expect(screen.getByText("+2 seit dem letzten Stand")).toHaveClass("text-muted");
    cleanup();
    await renderWidget("core.counter", preset("mine_open"), { status: "ok", data: { value: 5, delta: -2, spark: [7, 5] } });
    expect(screen.getByText("−2 seit dem letzten Stand")).toHaveClass("text-muted");
    expect(screen.getByText("5")).toHaveClass("text-ink");
  });

  it("shows no delta text and no sparkline without history", async () => {
    const { container } = await renderWidget("core.counter", preset("mine_open"), {
      status: "ok",
      data: { value: 4, delta: null, spark: [4] },
    });
    expect(screen.getByText("4")).toBeVisible();
    expect(screen.queryByText(/seit dem letzten Stand/)).toBeNull();
    expect(container.querySelector("polyline")).toBeNull();
  });

  it("uses the host's title override as label; outside a stored dashboard it is no link", async () => {
    await renderWidget("core.counter", preset("mine_new"), { status: "ok", data: { value: 3, delta: 0, spark: [3, 3] } }, "view", {
      titleOverride: "Neu bei mir",
    });
    expect(screen.getByText("Neu bei mir")).toBeVisible();
    expect(screen.getByText("3")).toBeVisible();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("links the whole tile to the drill page in view and TV mode, not in edit or preview", async () => {
    const data: WidgetDataState = { status: "ok", data: { value: 3, delta: 0, spark: [3, 3] } };
    await renderWidget("core.counter", preset("mine_new"), data, "view", { dashboardId: 7, titleOverride: "Neu bei mir" });
    const link = screen.getByRole("link", { name: "Tickets zu „Neu bei mir“" });
    const href = link.getAttribute("href") ?? "";
    expect(href).toContain("/agent/tickets/drill");
    expect(href).toContain("d=7");
    expect(href).toContain("w=w-test");
    expect(href).not.toContain("filter");
    cleanup();
    await renderWidget("core.counter", preset("mine_new"), data, "wallboard", { dashboardId: 7 });
    expect(screen.getByRole("link", { name: /Tickets zu/ })).toBeInTheDocument();
    for (const mode of ["edit", "preview"] as const) {
      cleanup();
      await renderWidget("core.counter", preset("mine_new"), data, mode, { dashboardId: 7 });
      expect(screen.queryByRole("link")).toBeNull();
    }
  });
});

describe("wallboard titles without details (no source on the wire)", () => {
  const ok: WidgetDataState = { status: "ok", data: { value: 3, delta: 0, spark: [3, 3] } };
  it("titles a counter by the preset name the server sent", async () => {
    await renderWidget("core.counter", { preset: "unowned_new" }, ok, "wallboard", {}, () => {}, { kiosk: true });
    expect(screen.getByText("Zum Übernehmen")).toBeInTheDocument();
    expect(screen.queryByText("Meine offenen")).toBeNull();
  });
  it("does not fall back to Meine offenen for a custom source (no preset sent)", async () => {
    await renderWidget("core.counter", {}, ok, "wallboard", {}, () => {}, { kiosk: true });
    expect(screen.queryByText("Meine offenen")).toBeNull();
  });
  it("titles a ticket list by the preset name the server sent", async () => {
    await renderWidget(
      "core.ticket-list",
      { preset: "escalated" },
      { status: "ok", data: { items: [], total: 0 } },
      "wallboard",
      {},
      () => {},
      { kiosk: true },
    );
    expect(screen.queryByText("Meine offenen Tickets")).toBeNull();
  });
});

// ---------------------------------------------------------------- ticket list

describe("core.ticket-list", () => {
  it("renders the configured columns only, in order, with tn+title as one Ticket column, and the density class", async () => {
    await renderWidget(
      "core.ticket-list",
      { ...preset("mine_open"), columns: ["tn", "title", "queue"], density: "compact" },
      { status: "ok", data: { items: [ticket(1, { queue_name: "Support::2nd Level" })], total: 1 } },
    );
    expect(screen.getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Ticket", "Queue"]);
    const table = screen.getByTestId("ticket-list-table");
    expect(table).toHaveClass("density-compact");
    const cells = within(table).getAllByRole("cell");
    expect(cells).toHaveLength(2);
    expect(cells[0]).toHaveTextContent("2026100912000001");
    expect(cells[0]).toHaveTextContent("Ticket 1");
    expect(cells[1]).toHaveTextContent("2nd Level");
  });

  it("keeps separate headers when only one of number and title is chosen", async () => {
    await renderWidget("core.ticket-list", { ...preset("mine_open"), columns: ["title", "queue"] }, { status: "ok", data: { items: [ticket(1)], total: 1 } });
    expect(screen.getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Titel", "Queue"]);
    cleanup();
    await renderWidget("core.ticket-list", { ...preset("mine_open"), columns: ["queue", "tn", "state"] }, { status: "ok", data: { items: [ticket(1)], total: 1 } });
    expect(screen.getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Queue", "Ticketnummer", "Status"]);
  });

  it("defaults to the mockup's columns and comfortable density", async () => {
    await renderWidget("core.ticket-list", preset("mine_open"), { status: "ok", data: { items: [ticket(1)], total: 1 } });
    expect(screen.getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Ticket", "Kunde", "Status", "Fällig"]);
    expect(screen.getByTestId("ticket-list-table")).toHaveClass("density-comfortable");
  });

  it("shows the elapsed time since a breach in danger and the time left before a deadline", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-10T12:00:00Z"));
    const now = Date.parse("2026-10-10T12:00:00Z") / 1000;
    await renderWidget(
      "core.ticket-list",
      { ...preset("mine_open"), columns: ["title", "due"] },
      {
        status: "ok",
        data: {
          items: [
            ticket(1, { escalation_solution_time: now - (3 * 3600 + 20 * 60) }),
            ticket(2, { escalation_response_time: now + 25 * 60 }),
            ticket(3),
            ticket(4, { escalation_update_time: now + 45 * 60 }),
          ],
          total: 4,
        },
      },
    );
    expect(screen.getByTestId("ticket-1-due")).toHaveTextContent("seit 3h20m");
    expect(screen.getByTestId("ticket-1-due")).toHaveClass("text-danger");
    expect(screen.getByTestId("ticket-2-due")).toHaveTextContent("in 25 Min.");
    expect(screen.getByTestId("ticket-2-due")).not.toHaveClass("text-danger");
    // the app's warning window (escalationLevel, 30 min): inside it escalation colour, beyond it muted
    expect(screen.getByTestId("ticket-2-due")).toHaveClass("text-escalation");
    expect(screen.getByTestId("ticket-4-due")).toHaveTextContent("in 45 Min.");
    expect(screen.getByTestId("ticket-4-due")).toHaveClass("text-muted");
    expect(screen.getByTestId("ticket-3-due")).toHaveTextContent("—");
  });

  it("phone rows show title, customer and due like the Mobile mockup", async () => {
    await renderWidget("core.ticket-list", preset("mine_open"), {
      status: "ok",
      data: { items: [ticket(7, { customer_email: "kunde@example.org" })], total: 1 },
    });
    expect(screen.getByTestId("dashboard-ticket-7-customer")).toHaveTextContent("kunde@example.org");
  });

  it("footer 'Alle 12 anzeigen' appears only when total > rows and always links to the drill page", async () => {
    const drill = { dashboardId: 7 };
    for (const source of [
      { kind: "preset", preset: "mine_new" },
      { kind: "preset", preset: "mine_open" },
      { kind: "preset", preset: "escalated" },
      { kind: "saved_search", id: 9 },
      { kind: "filter", filter: { version: 1 } },
    ]) {
      cleanup();
      await renderWidget("core.ticket-list", { source, limit: 8 }, { status: "ok", data: { items: rows(8), total: 12 } }, "view", drill);
      const href = screen.getByRole("link", { name: "Alle 12 anzeigen" }).getAttribute("href") ?? "";
      expect(href).toContain("/agent/tickets/drill");
      expect(href).toContain("d=7");
      expect(href).toContain("w=w-test");
    }
    cleanup();
    await renderWidget("core.ticket-list", preset("mine_new"), { status: "ok", data: { items: rows(3), total: 3 } }, "view", drill);
    expect(screen.queryByText(/Alle \d+ anzeigen/)).toBeNull();
    expect(screen.queryByText(/insgesamt/)).toBeNull();
  });

  it("without a stored dashboard (or in preview) the total is plain text", async () => {
    await renderWidget("core.ticket-list", preset("mine_open"), { status: "ok", data: { items: rows(8), total: 12 } });
    expect(screen.getByText("12 insgesamt")).toBeVisible();
    expect(screen.queryByRole("link", { name: /anzeigen/ })).toBeNull();
    cleanup();
    await renderWidget("core.ticket-list", preset("mine_open"), { status: "ok", data: { items: rows(8), total: 12 } }, "preview", { dashboardId: 7 });
    expect(screen.getByText("12 insgesamt")).toBeVisible();
    expect(screen.queryByRole("link", { name: /anzeigen/ })).toBeNull();
  });

  it("keeps a saved-search or filter source instead of resetting it to the default preset", async () => {
    const def = DASHBOARD_WIDGETS.get("core.ticket-list")!;
    expect(settingsWithDefaults(def, { source: { kind: "saved_search", id: 9 } }, 1).source).toEqual({ kind: "saved_search", id: 9 });
    const flt = { kind: "filter", filter: { version: 1 } };
    expect(settingsWithDefaults(def, { source: flt }, 1).source).toEqual(flt);
  });

  it("a forbidden tile shows the message; without an edit handler there is no 'Suche wählen' (the real wiring is tested in DashboardPage)", async () => {
    const state: WidgetDataState = { status: "forbidden", error: "Die Suche gibt es nicht mehr oder ist für Sie nicht freigegeben." };
    await renderWidget("core.counter", { source: { kind: "saved_search", id: 9 } }, state);
    expect(screen.getByText(/gibt es nicht mehr/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Suche wählen" })).toBeNull();
  });

  it("a counter on a saved search is titled by the type, not by a preset", async () => {
    await renderWidget("core.counter", { source: { kind: "saved_search", id: 9 } }, { status: "ok", data: { value: 4, delta: null, spark: [] } });
    expect(screen.getByRole("heading", { name: "Kennzahl" })).toBeVisible();
    expect(screen.getByText("4")).toBeVisible();
  });

  it("titles itself after the preset unless the host overrides it", async () => {
    await renderWidget("core.ticket-list", preset("mine_open"), { status: "ok", data: { items: [], total: 0 } });
    expect(screen.getByRole("heading", { name: "Meine offenen Tickets" })).toBeVisible();
    cleanup();
    await renderWidget("core.ticket-list", preset("unowned_new"), { status: "ok", data: { items: [], total: 0 } }, "view", {
      titleOverride: "Team-Eingang (zum Übernehmen)",
    });
    expect(screen.getByRole("heading", { name: "Team-Eingang (zum Übernehmen)" })).toBeVisible();
  });

  it("empty state says what to do; the action only for viewers who can edit", async () => {
    const onConfigure = vi.fn();
    await renderWidget("core.ticket-list", preset("mine_open"), { status: "ok", data: { items: [], total: 0 } }, "view", {
      canEdit: true,
      onConfigure,
    });
    expect(screen.getByText(/Noch keine Tickets in dieser Suche/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Filter ändern" }));
    expect(onConfigure).toHaveBeenCalledTimes(1);
    cleanup();
    await renderWidget("core.ticket-list", preset("mine_open"), { status: "ok", data: { items: [], total: 0 } });
    expect(screen.getByText(/Noch keine Tickets in dieser Suche/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Filter ändern" })).toBeNull();
  });
});

// ---------------------------------------------------------------- escalations

describe("core.escalations", () => {
  it("shows kind, customer and the signed time: since a breach in danger, time left before it", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-10T12:00:00Z"));
    const past = Date.parse("2026-10-10T08:40:00Z") / 1000;
    const future = Date.parse("2026-10-10T12:25:00Z") / 1000;
    await renderWidget("core.escalations", {}, {
      status: "ok",
      data: {
        items: [
          { ticket: ticket(1, { title: "VPN gesperrt", customer_id: "Beispiel GmbH", customer_email: null }), epoch: past, kind: "solution" },
          { ticket: ticket(2, { title: "Backup-Fehler" }), epoch: future, kind: "first_response" },
        ],
        total: 2,
        oldest_epoch: past,
      },
    });
    expect(screen.getByText("VPN gesperrt")).toBeVisible();
    expect(screen.getByText("Lösungszeit · Beispiel GmbH")).toBeVisible();
    expect(screen.getByText("seit 3h20m")).toHaveClass("text-danger");
    expect(screen.getByText("noch 25 Min.")).toHaveClass("text-escalation");
    expect(screen.getByText(/^Erste Antwort · /)).toBeVisible();
  });

  it("the header link opens the drill page with the widget's total; none while empty", async () => {
    const items = [{ ticket: ticket(1), epoch: 1, kind: "solution" }];
    await renderWidget("core.escalations", {}, { status: "ok", data: { items, total: 5, oldest_epoch: 1 } }, "view", { dashboardId: 7 });
    expect(screen.getByRole("link", { name: "Alle 5 anzeigen" }).getAttribute("href")).toContain("/agent/tickets/drill?d=7&w=w-test");
    cleanup();
    await renderWidget("core.escalations", {}, { status: "ok", data: { items: [], total: 0, oldest_epoch: null } }, "view", { dashboardId: 7 });
    expect(screen.queryByRole("link", { name: /anzeigen/ })).toBeNull();
  });

  it("has an empty state that matches the setting", async () => {
    await renderWidget("core.escalations", { within_hours: 0 }, { status: "ok", data: { items: [], total: 0, oldest_epoch: null } });
    expect(screen.getByText("Keine eskalierten Tickets.")).toBeVisible();
    cleanup();
    await renderWidget("core.escalations", {}, { status: "ok", data: { items: [], total: 0, oldest_epoch: null } });
    expect(screen.getByText("Keine Fristen in den nächsten 8 Stunden.")).toBeVisible();
  });
});

// ---------------------------------------------------------------- reminders

describe("core.reminders", () => {
  it("the header link opens the drill page with the widget's total", async () => {
    await renderWidget("core.reminders", {}, { status: "ok", data: { items: [{ ticket: ticket(1), until: 1, overdue: true }], total: 4 } }, "view", {
      dashboardId: 7,
    });
    expect(screen.getByRole("link", { name: "Alle 4 anzeigen" }).getAttribute("href")).toContain("/agent/tickets/drill?d=7&w=w-test");
  });

  it("colours overdue entries danger", async () => {
    await renderWidget("core.reminders", {}, {
      status: "ok",
      data: {
        items: [
          { ticket: ticket(1), until: 1, overdue: true },
          { ticket: ticket(2), until: 4e9, overdue: false },
        ],
        total: 2,
      },
    });
    expect(screen.getByTestId("reminder-1-due")).toHaveClass("text-danger");
    expect(screen.getByTestId("reminder-2-due")).not.toHaveClass("text-danger");
    // not by colour only
    expect(screen.getByTestId("reminder-1-due")).toHaveTextContent("Überfällig");
    expect(screen.getByTestId("reminder-2-due")).not.toHaveTextContent("Überfällig");
    expect(screen.getByText("Ticket 1")).toBeVisible();
  });

  it("shows an empty state", async () => {
    await renderWidget("core.reminders", {}, { status: "ok", data: { items: [], total: 0 } });
    expect(screen.getByText("Keine fälligen Erinnerungen.")).toBeVisible();
  });
});

// ---------------------------------------------------------------- calendar agenda

describe("core.calendar-agenda", () => {
  it("lists times in the viewer's zone and links to the calendar", async () => {
    await renderWidget("core.calendar-agenda", {}, { status: "ok", data: { items: [occ({ title: "Rückruf", start_time: "2026-10-10T08:00:00Z" })] } });
    expect(screen.getByText("10:00")).toBeVisible();
    expect(screen.getByText("Rückruf")).toBeVisible();
    expect(screen.getByRole("link", { name: /Kalender/ })).toHaveAttribute("href", "/agent/calendar");
  });

  it("reads the backend's zone-less times as UTC and shows only the date for all-day entries", async () => {
    await renderWidget("core.calendar-agenda", {}, {
      status: "ok",
      data: {
        items: [
          occ({ appointment_id: 1, title: "Daily", start_time: "2026-10-10T07:15:00", end_time: "2026-10-10T07:30:00" }),
          occ({ appointment_id: 2, title: "Betriebsausflug", start_time: "2026-10-10T00:00:00", end_time: "2026-10-11T00:00:00", all_day: true }),
        ],
      },
    });
    expect(screen.getByText("09:15")).toBeVisible();
    expect(screen.getByTestId("agenda-2-time")).toHaveTextContent("10.10.");
    expect(screen.getByTestId("agenda-2-time")).not.toHaveTextContent(":");
  });

  it("has an empty state", async () => {
    await renderWidget("core.calendar-agenda", {}, { status: "ok", data: { items: [] } });
    expect(screen.getByText("Heute keine Termine.")).toBeVisible();
  });
});

// ---------------------------------------------------------------- team presence

describe("core.team-presence", () => {
  it("shows avatars with initials and the online count", async () => {
    await renderWidget("core.team-presence", {}, { status: "ok", data: { online: [agent("Clara Lenz"), agent("Ben Krause")], total_online: 2 } });
    expect(screen.getByText("CL")).toBeVisible();
    expect(screen.getByText("Ben Krause")).toBeVisible();
    expect(screen.getByText("2 online")).toBeVisible();
  });

  it("shows 'im Chat' for agents in a chat", async () => {
    const inChat = { ...agent("Clara Lenz", 7), status: "in_chat" as const };
    const free = { ...agent("Ben Krause", 8), status: "available" as const };
    await renderWidget("core.team-presence", {}, { status: "ok", data: { online: [inChat, free], total_online: 2 } });
    expect(screen.getByTestId("presence-in-chat-7")).toHaveTextContent("im Chat");
    expect(screen.queryByTestId("presence-in-chat-8")).toBeNull();
  });

  it("shows per-status counts when the wallboard sends no people", async () => {
    await renderWidget("core.team-presence", {}, { status: "ok", data: { total_online: 3, by_status: { available: 2, in_chat: 1 } } });
    expect(screen.getByTestId("team-presence-counts")).toHaveTextContent("2 verfügbar");
    expect(screen.getByTestId("team-presence-counts")).toHaveTextContent("1 im Chat");
  });

  it("has an empty state", async () => {
    await renderWidget("core.team-presence", {}, { status: "ok", data: { online: [], total_online: 0 } });
    expect(screen.getByText("Gerade ist niemand online.")).toBeVisible();
  });
});

// ---------------------------------------------------------------- queue overview

describe("core.queue-overview", () => {
  const row = (id: number, name: string, open: number, fresh: number, escalated = 0) => ({ id, name, open, new: fresh, escalated });

  it("renders server data with an escalated segment and makes no queues request", async () => {
    const listQueues = vi.spyOn(api, "listQueues");
    await renderWidget(
      "core.queue-overview",
      {},
      { status: "ok", data: { queues: [row(1, "Support", 38, 6, 5), row(3, "Buchhaltung::Mahnwesen", 9, 2)], total: 2 } },
    );
    expect(listQueues).not.toHaveBeenCalled();
    expect(screen.getByText("Support")).toBeVisible();
    expect(screen.getByText("38 offen · 6 neu")).toBeVisible();
    expect(screen.getByTestId("queue-escalated-1")).toHaveTextContent("5 eskaliert");
    expect(screen.queryByTestId("queue-escalated-3")).toBeNull();
    expect(screen.getByText("Mahnwesen")).toBeVisible();
    // widths: max total is 44; open-without-escalated 33, new 6, escalated 5
    const segments = Array.from(screen.getByTestId("queue-row-1").querySelectorAll<HTMLElement>("[data-segment]"));
    expect(segments.map((el) => el.dataset.segment)).toEqual(["open", "new", "escalated"]);
    expect(segments.map((el) => el.style.width)).toEqual(["75%", "13.6%", "11.4%"]);
    expect(screen.getByTestId("queue-overview-legend")).toHaveTextContent("offen");
    expect(screen.getByTestId("queue-overview-legend")).toHaveTextContent("neu");
    expect(screen.getByTestId("queue-overview-legend")).toHaveTextContent("eskaliert");
  });

  it("keeps the server order", async () => {
    await renderWidget(
      "core.queue-overview",
      { only_with_work: false },
      { status: "ok", data: { queues: [row(4, "Support::2nd Level", 7, 1), row(1, "Support", 3, 0), row(2, "Leer", 0, 0)], total: 3 } },
    );
    const ids = screen.getAllByTestId(/^queue-row-\d+$/).map((el) => el.getAttribute("data-testid"));
    expect(ids).toEqual(["queue-row-4", "queue-row-1", "queue-row-2"]);
    expect(screen.getByText("2nd Level")).toBeVisible();
  });

  it("says so when no queue has work, and when none is visible", async () => {
    await renderWidget("core.queue-overview", {}, { status: "ok", data: { queues: [], total: 0 } });
    expect(screen.getByText("Keine Queues mit offenen oder neuen Tickets.")).toBeVisible();
    cleanup();
    await renderWidget("core.queue-overview", { only_with_work: false }, { status: "ok", data: { queues: [], total: 0 } });
    expect(screen.getByText("Keine Queues sichtbar.")).toBeVisible();
  });
});

// ---------------------------------------------------------------- chat queue

describe("core.chat-queue", () => {
  const minutesAgo = (m: number) => new Date(Date.now() - m * 60_000).toISOString();
  const item = (id: number, title: string, m: number) => ({
    session_id: id,
    ticket_id: 1000 + id,
    tn: `T${id}`,
    title,
    topic_label: "Support",
    waiting_since: minutesAgo(m),
  });

  it("shows the waiting count, the oldest wait, my chats and the items as ticket links", async () => {
    await renderWidget(
      "core.chat-queue",
      {},
      {
        status: "ok",
        data: { enabled: true, waiting: 2, oldest_waiting_since: minutesAgo(7), mine: 1, items: [item(1, "Rechnung fehlt", 7), item(2, "Login", 3)] },
      },
    );
    expect(screen.getByTestId("chat-waiting")).toHaveTextContent("2");
    expect(screen.getByText("warten")).toBeVisible();
    expect(screen.getByTestId("chat-mine")).toHaveTextContent("1");
    expect(screen.getByText("Meine Chats")).toBeVisible();
    expect(screen.getByTestId("chat-oldest")).toHaveTextContent("Der älteste wartet seit 7 Min.");
    expect(screen.getByTestId("chat-item-1")).toHaveTextContent("Rechnung fehlt");
    expect(screen.getByTestId("chat-item-1")).toHaveTextContent("wartet seit 7 Min.");
    expect(screen.getByTestId("chat-item-1").closest("a")).not.toBeNull();
  });

  it("caps the item list at the limit setting and shows 200+ at the inbox cap", async () => {
    await renderWidget(
      "core.chat-queue",
      { limit: 1 },
      { status: "ok", data: { enabled: true, waiting: 200, oldest_waiting_since: minutesAgo(90), mine: 0, items: [item(1, "A", 90), item(2, "B", 80)] } },
    );
    expect(screen.getByTestId("chat-waiting")).toHaveTextContent("200+");
    expect(screen.getByTestId("chat-item-1")).toBeVisible();
    expect(screen.queryByTestId("chat-item-2")).toBeNull();
  });

  it("shows counts only on a wallboard without details (no items, no 'Meine Chats')", async () => {
    await renderWidget("core.chat-queue", {}, { status: "ok", data: { enabled: true, waiting: 3, oldest_waiting_since: minutesAgo(2) } }, "wallboard", {}, () => {}, {
      kiosk: true,
    });
    expect(screen.getByTestId("chat-waiting")).toHaveTextContent("3");
    expect(screen.queryByTestId(/^chat-item-/)).toBeNull();
    expect(screen.queryByTestId("chat-mine")).toBeNull();
    expect(screen.queryByText("Meine Chats")).toBeNull();
  });

  it("never shows 'Meine Chats' on a wallboard link, even if an older server sent the count", async () => {
    await renderWidget("core.chat-queue", {}, { status: "ok", data: { enabled: true, waiting: 1, oldest_waiting_since: minutesAgo(2), mine: 4 } }, "wallboard", {}, () => {}, {
      kiosk: true,
    });
    expect(screen.queryByTestId("chat-mine")).toBeNull();
  });

  it("counts the chats the assistant answers apart from the waiting ones, also on a wallboard", async () => {
    await renderWidget("core.chat-queue", {}, { status: "ok", data: { enabled: true, waiting: 1, oldest_waiting_since: minutesAgo(2), mine: 0, bot: 3, items: [item(1, "A", 2)] } });
    expect(screen.getByTestId("chat-waiting")).toHaveTextContent("1");
    expect(screen.getByTestId("chat-bot")).toHaveTextContent("Mit KI im Gespräch: 3");
    cleanup();
    await renderWidget("core.chat-queue", {}, { status: "ok", data: { enabled: true, waiting: 0, oldest_waiting_since: null, bot: 250 } }, "wallboard", {}, () => {}, {
      kiosk: true,
    });
    expect(screen.getByTestId("chat-bot")).toHaveTextContent("Mit KI im Gespräch: 200+");
    expect(screen.queryByTestId(/^chat-item-/)).toBeNull();
  });

  it("shows no assistant line when no chat is with the AI or an older server sends no count", async () => {
    await renderWidget("core.chat-queue", {}, { status: "ok", data: { enabled: true, waiting: 2, oldest_waiting_since: minutesAgo(3), mine: 0, bot: 0, items: [] } });
    expect(screen.queryByTestId("chat-bot")).toBeNull();
    cleanup();
    await renderWidget("core.chat-queue", {}, { status: "ok", data: { enabled: true, waiting: 2, oldest_waiting_since: minutesAgo(3), mine: 0, items: [] } });
    expect(screen.queryByTestId("chat-bot")).toBeNull();
  });

  it("says so when the web chat is not set up", async () => {
    await renderWidget("core.chat-queue", {}, { status: "ok", data: { enabled: false, waiting: 0, oldest_waiting_since: null, mine: 0, items: [] } });
    expect(screen.getByText("Web-Chat ist nicht eingerichtet.")).toBeVisible();
    expect(screen.queryByTestId("chat-waiting")).toBeNull();
  });

  it("is refreshed by chat_waiting, chat_assigned and chat_ended", () => {
    const refresh = DASHBOARD_WIDGETS.get("core.chat-queue")!.refreshOn;
    for (const type of ["chat_waiting", "chat_assigned", "chat_ended"]) expect(refresh).toContain(type);
  });
});

// ---------------------------------------------------------------- AI status

describe("core.ai-status", () => {
  const budget = (over: Record<string, unknown> = {}) => ({
    kind: "queue_tokens_day",
    subject_name: "Support",
    window: "day",
    used: 1_900_000,
    limit: 3_000_000,
    exhausted: false,
    resets_at: "2026-10-13T00:00:00Z",
    currency: null,
    ...over,
  });

  it("shows the escalated count, the runs today and a budget bar per queue", async () => {
    await renderWidget(
      "core.ai-status",
      {},
      { status: "ok", data: { ai_escalated: 3, runs_today: { auto_reply: 14, summary: 31 }, budgets: [budget()] } },
    );
    expect(screen.getByTestId("ai-escalated")).toHaveTextContent("3");
    expect(screen.getByText("An Menschen übergeben")).toBeVisible();
    expect(screen.getByTestId("ai-runs")).toHaveTextContent("45");
    expect(screen.getByText("KI-Läufe heute")).toBeVisible();
    const bar = within(screen.getByTestId("ai-budget-0")).getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "63");
    expect(screen.getByTestId("ai-budget-0")).toHaveAttribute("data-tone", "ok");
    expect(screen.getByTestId("ai-budget-0")).toHaveTextContent("1,1 Mio. verfügbar");
  });

  it("marks an exhausted budget in the danger tone", async () => {
    await renderWidget(
      "core.ai-status",
      {},
      {
        status: "ok",
        data: {
          ai_escalated: 0,
          runs_today: {},
          budgets: [budget({ subject_name: "Buchhaltung", used: 3_000_000, exhausted: true })],
        },
      },
    );
    const row = screen.getByTestId("ai-budget-0");
    expect(row).toHaveAttribute("data-tone", "danger");
    expect(row).toHaveTextContent("aufgebraucht");
    expect(within(row).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
    expect(row.querySelector(".bg-danger")).not.toBeNull();
  });

  it("renders without budgets (wallboard without details)", async () => {
    await renderWidget("core.ai-status", {}, { status: "ok", data: { ai_escalated: 0, runs_today: { draft: 2 } } });
    expect(screen.getByTestId("ai-runs")).toHaveTextContent("2");
    expect(screen.queryByRole("progressbar")).toBeNull();
  });
});

// ---------------------------------------------------------------- recent views

describe("core.recent-views", () => {
  it("reads localStorage (getLastViews) and shows an empty state without entries", async () => {
    localStorage.setItem("tiqora.lastViews", JSON.stringify([{ id: 5, tn: "2026100912000231", title: "VPN", at: Date.now() - 60_000 }]));
    await renderWidget("core.recent-views", {}, { status: "none" });
    expect(screen.getByText("VPN")).toBeVisible();
    expect(screen.getByRole("link", { name: /VPN/ })).toHaveAttribute("href", "/agent/tickets/5");
    cleanup();
    localStorage.clear();
    await renderWidget("core.recent-views", {}, { status: "none" });
    expect(screen.getByText("Noch nichts angesehen.")).toBeVisible();
  });

  it("caps the list at the configured limit", async () => {
    const entries = Array.from({ length: 6 }, (_, i) => ({ id: i + 1, tn: `T${i + 1}`, title: `Gesehen ${i + 1}`, at: Date.now() }));
    localStorage.setItem("tiqora.lastViews", JSON.stringify(entries));
    await renderWidget("core.recent-views", { limit: 2 }, { status: "none" });
    expect(screen.getAllByRole("link")).toHaveLength(2);
  });
});

// ---------------------------------------------------------------- notes

describe("core.notes", () => {
  it("renders sanitised markdown", async () => {
    const w = window as unknown as { __x?: number };
    await renderWidget("core.notes", { markdown: "**fett** <script>window.__x=1</script> [a](javascript:alert(1)) <img src=x onerror=\"window.__x=2\">" }, { status: "none" });
    expect(screen.getByText("fett").tagName).toBe("STRONG");
    expect(document.querySelector("script")).toBeNull();
    expect(document.querySelector("[onerror]")).toBeNull();
    expect(document.querySelector('a[href^="javascript:"]')).toBeNull();
    await act(async () => {});
    expect(w.__x).toBeUndefined();
  });

  it("drops forms, inputs, images and inline styles from shared notes; links open in a new tab", async () => {
    await renderWidget(
      "core.notes",
      {
        markdown:
          'Bitte anmelden:\n\n<form action="https://evil.example.org/x" method="post"><input name="pw" type="password"><button>OK</button></form>\n\n<img src="https://t.example.org/p.gif"> <span style="position:fixed">x</span> [Wiki](https://example.org/wiki)',
      },
      { status: "none" },
    );
    const body = screen.getByTestId("notes-markdown");
    expect(body.querySelector("form")).toBeNull();
    expect(body.querySelector("input")).toBeNull();
    expect(body.querySelector("button")).toBeNull();
    expect(body.querySelector("img")).toBeNull();
    expect(body.querySelector("[style]")).toBeNull();
    expect(screen.getByText("Bitte anmelden:")).toBeVisible();
    const wiki = screen.getByRole("link", { name: "Wiki" });
    expect(wiki).toHaveAttribute("target", "_blank");
    expect(wiki).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("drops SVG and MathML from shared notes, so no foreign element can load a URL", async () => {
    const probes = [
      '<svg><image href="https://evil.example.org/a.png" /></svg>',
      '<svg><image xlink:href="https://evil.example.org/b.png" /></svg>',
      '<svg><filter id="f"><feImage href="https://evil.example.org/c.png" /></filter></svg>',
      '<math><mi xlink:href="https://evil.example.org/d">x</mi></math>',
    ];
    await renderWidget("core.notes", { markdown: `Vorher\n\n${probes.join("\n\n")}\n\nNachher` }, { status: "none" });
    const body = screen.getByTestId("notes-markdown");
    for (const tag of ["svg", "image", "feImage", "filter", "math", "mi"]) {
      expect(body.getElementsByTagName(tag)).toHaveLength(0);
    }
    expect(body.innerHTML).not.toContain("evil.example.org");
    expect(screen.getByText("Vorher")).toBeVisible();
    expect(screen.getByText("Nachher")).toBeVisible();
  });

  it("keeps links, tables and ordinary markup in shared notes", async () => {
    await renderWidget(
      "core.notes",
      {
        markdown: [
          "## Übersicht",
          "[Wiki](https://example.org/wiki) [Queues](/agent/queues) [Team](mailto:team@example.org)",
          "| Kopf | Wert |\n| --- | --- |\n| a | 1 |",
          "- eins\n- zwei",
          "> Zitat ~~alt~~",
          "```\ncode\n```",
          "<details><summary>Mehr</summary>Details</details>",
          "---",
        ].join("\n\n"),
      },
      { status: "none" },
    );
    const body = screen.getByTestId("notes-markdown");
    expect(screen.getByRole("heading", { name: "Übersicht" })).toBeVisible();
    expect(screen.getByRole("link", { name: "Wiki" })).toHaveAttribute("href", "https://example.org/wiki");
    expect(screen.getByRole("link", { name: "Queues" })).toHaveAttribute("href", "/agent/queues");
    expect(screen.getByRole("link", { name: "Team" })).toHaveAttribute("href", "mailto:team@example.org");
    for (const tag of ["table", "thead", "th", "td", "ul", "li", "blockquote", "del", "pre", "code", "details", "summary", "hr"]) {
      expect(body.querySelector(tag), tag).not.toBeNull();
    }
  });

  it("has an empty state", async () => {
    await renderWidget("core.notes", {}, { status: "none" });
    expect(screen.getByText(/Noch keine Notiz/)).toBeVisible();
  });
});

// ---------------------------------------------------------------- links

describe("core.links", () => {
  const items = [
    { label: "Wiki", url: "https://example.org/wiki" },
    { label: "Böse", url: "javascript:alert(1)" },
    { label: "Auch böse", url: "//evil.example.org" },
    { label: "Mail", url: "mailto:support@example.org" },
    { label: "Intern", url: "/agent/queues" },
  ];

  it("renders only safe urls; external links open in a new tab with noopener noreferrer", async () => {
    await renderWidget("core.links", { items }, { status: "none" });
    const wiki = screen.getByRole("link", { name: "Wiki" });
    expect(wiki).toHaveAttribute("href", "https://example.org/wiki");
    expect(wiki).toHaveAttribute("target", "_blank");
    expect(wiki).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.queryByText("Böse")).toBeNull();
    expect(screen.queryByText("Auch böse")).toBeNull();
    expect(screen.getByRole("link", { name: "Mail" })).not.toHaveAttribute("target");
    const intern = screen.getByRole("link", { name: "Intern" });
    expect(intern).toHaveAttribute("href", "/agent/queues");
    expect(intern).not.toHaveAttribute("target");
  });

  it("in edit mode nothing inside a widget is a tab stop or navigates", async () => {
    await renderWidget("core.links", { items: [{ label: "Wiki", url: "https://example.org" }] }, { status: "none" }, "edit");
    expect(screen.getByRole("link", { name: "Wiki", hidden: true })).toHaveAttribute("tabindex", "-1");
    expect(screen.getByTestId("widget-body")).toHaveClass("pointer-events-none");
  });

  it("in preview mode renders no links at all", async () => {
    await renderWidget("core.links", { items: [{ label: "Wiki", url: "https://example.org" }] }, { status: "none" }, "preview");
    expect(screen.getByText("Wiki")).toBeVisible();
    expect(screen.queryByRole("link", { hidden: true })).toBeNull();
  });
});

describe("edit and preview modes across widgets", () => {
  it("ticket rows are not tab stops in edit mode and not links in preview", async () => {
    await renderWidget("core.ticket-list", preset("mine_new"), { status: "ok", data: { items: rows(8), total: 12 } }, "edit");
    for (const a of screen.getAllByRole("link", { hidden: true })) expect(a).toHaveAttribute("tabindex", "-1");
    cleanup();
    await renderWidget("core.ticket-list", preset("mine_new"), { status: "ok", data: { items: rows(8), total: 12 } }, "preview");
    expect(screen.queryByRole("link", { hidden: true })).toBeNull();
    expect(screen.getAllByText("Ticket 1").length).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------- wallboard styles (Task 19)

describe("wallboard mode", () => {
  it("counter: 96px mono value, a sub line with the change, no sparkline", async () => {
    await renderWidget("core.counter", preset("mine_open"), { status: "ok", data: { value: 12, delta: 6, spark: [1, 2, 3, 4] } }, "wallboard");
    const value = screen.getByTestId("counter-value");
    expect(value).toHaveTextContent("12");
    expect(value.className).toContain("96px");
    expect(value).toHaveClass("font-mono");
    expect(screen.getByTestId("counter-sub")).toHaveTextContent("+6 seit dem letzten Stand");
    expect(screen.getByTestId("counter-sub").className).toContain("20px");
    expect(document.querySelector("svg polyline")).toBeNull();
    cleanup();
    // the same data in view mode still draws the sparkline
    await renderWidget("core.counter", preset("mine_open"), { status: "ok", data: { value: 12, delta: 6, spark: [1, 2, 3, 4] } }, "view");
    expect(document.querySelector("svg")).not.toBeNull();
  });

  it("escalations at h <= 2 render the single 'Nächste Frist' banner, taller ones the list", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-10T12:00:00Z"));
    const soon = Date.parse("2026-10-10T12:25:00Z") / 1000;
    const later = Date.parse("2026-10-10T15:00:00Z") / 1000;
    const data = {
      status: "ok" as const,
      data: {
        items: [
          { ticket: ticket(1, { title: "VPN gesperrt", customer_id: "Beispiel GmbH", customer_email: null }), epoch: soon, kind: "solution" },
          { ticket: ticket(2, { title: "Backup-Fehler" }), epoch: later, kind: "first_response" },
        ],
        total: 2,
        oldest_epoch: soon,
      },
    };
    await renderWidget("core.escalations", {}, data, "wallboard", {}, () => {}, { size: { w: 4, h: 2 } });
    const banner = screen.getByTestId("escalation-banner");
    expect(banner).toHaveTextContent("Nächste Frist: VPN gesperrt · Beispiel GmbH · in 25 Min.");
    expect(banner).toHaveClass("text-danger");
    expect(screen.queryByText("Backup-Fehler")).toBeNull();
    cleanup();
    await renderWidget("core.escalations", {}, data, "wallboard", {}, () => {}, { size: { w: 4, h: 4 } });
    expect(screen.queryByTestId("escalation-banner")).toBeNull();
    expect(screen.getByText("Backup-Fehler")).toBeVisible();
  });

  it("escalations banner without details names the ticket number only", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-10T12:00:00Z"));
    const soon = Date.parse("2026-10-10T12:25:00Z") / 1000;
    await renderWidget(
      "core.escalations",
      {},
      { status: "ok", data: { items: [{ ticket: ticket(1, { title: null as unknown as string, tn: "2026101001", customer_id: null, customer_email: null }), epoch: soon, kind: "solution" }], total: 1, oldest_epoch: soon } },
      "wallboard",
      {},
      () => {},
      { size: { w: 4, h: 2 } },
    );
    expect(screen.getByTestId("escalation-banner")).toHaveTextContent("Nächste Frist: #2026101001 · in 25 Min.");
  });

  it("queue overview: name, three-segment bar, 'open / new neu' and the legend", async () => {
    await renderWidget(
      "core.queue-overview",
      {},
      { status: "ok", data: { queues: [{ id: 3, name: "Support::Netz", open: 82, new: 19, escalated: 4 }], total: 1 } },
      "wallboard",
    );
    const row = screen.getByTestId("queue-row-3");
    expect(row).toHaveTextContent("Netz");
    expect(row).toHaveTextContent("82 / 19 neu");
    expect(row.querySelector("[data-segment=open]")).not.toBeNull();
    expect(row.querySelector("[data-segment=new]")).not.toBeNull();
    expect(row.querySelector("[data-segment=escalated]")).not.toBeNull();
    expect(screen.getByTestId("queue-overview-legend")).toHaveTextContent("offen");
    expect(screen.getByTestId("queue-overview-legend")).toHaveTextContent("eskaliert");
  });

  it("team presence: two-column cards with a status dot and 'verfügbar' / 'im Chat'", async () => {
    await renderWidget(
      "core.team-presence",
      {},
      { status: "ok", data: { online: [{ ...agent("Clara Lenz", 1), status: "available" }, { ...agent("Ben Krause", 2), status: "in_chat" }], total_online: 2 } },
      "wallboard",
    );
    const free = screen.getByTestId("presence-card-1");
    const chat = screen.getByTestId("presence-card-2");
    expect(free).toHaveTextContent("Clara Lenz");
    expect(free).toHaveTextContent("verfügbar");
    expect(free.querySelector("[data-status=available]")).toHaveClass("bg-green");
    expect(chat).toHaveTextContent("im Chat");
    expect(chat.querySelector("[data-status=in_chat]")).toHaveClass("bg-accent");
  });

  it("no hover actions and, on a kiosk, no links at all", async () => {
    const items = [{ ticket: ticket(1), epoch: 1, kind: "solution" }];
    await renderWidget("core.escalations", {}, { status: "ok", data: { items, total: 5, oldest_epoch: 1 } }, "wallboard", { dashboardId: 7 }, () => {}, { size: { w: 4, h: 4 } });
    expect(screen.getAllByRole("link").length).toBeGreaterThan(0); // TV mode of a logged-in agent keeps links
    cleanup();
    await renderWidget("core.escalations", {}, { status: "ok", data: { items, total: 5, oldest_epoch: 1 } }, "wallboard", { dashboardId: 7 }, () => {}, { size: { w: 4, h: 4 }, kiosk: true });
    expect(screen.queryByRole("link")).toBeNull();
    cleanup();
    await renderWidget("core.ticket-list", preset("mine_new"), { status: "ok", data: { items: rows(4), total: 4 } }, "wallboard", { dashboardId: 7 }, () => {}, { kiosk: true });
    expect(screen.queryByRole("link", { hidden: true })).toBeNull();
    expect(screen.getAllByText("Ticket 1").length).toBeGreaterThan(0);
  });
});
