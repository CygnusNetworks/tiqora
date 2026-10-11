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
import type { OccurrenceOut, OnlineAgentOut, QueueNode, TicketListItem } from "@/lib/api";
import { setDisplayTimeZone } from "@/lib/timeZone";
import { DASHBOARD_WIDGETS, type WidgetMode } from "@/plugins/dashboardWidgets";
import { CORE_WIDGET_KEYS, settingsWithDefaults } from "../definitions";
import { WidgetDataProvider, type WidgetDataValue } from "../data";
import { WidgetChromeProvider, type WidgetChrome } from "../chrome/WidgetFrame";
import { registerCoreWidgets } from "./index";

vi.mock("@/auth/AuthContext", async () => {
  const actual = await vi.importActual<typeof import("@/auth/AuthContext")>("@/auth/AuthContext");
  return { ...actual, useOptionalAuth: () => ({ user: { id: 5 } }) };
});

// ---------------------------------------------------------------- fixtures and helpers

type WidgetDataState = Omit<WidgetDataValue, "refetch">;

/** The six core widgets that read provider data. */
const DATA_WIDGET_KEYS = [
  "core.ticket-list",
  "core.counter",
  "core.escalations",
  "core.reminders",
  "core.calendar-agenda",
  "core.team-presence",
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

function q(id: number, name: string, open: number, fresh: number): QueueNode {
  return { id, name, group_id: 1, valid: true, counts: { open, new: fresh, locked: 0, unlocked: open + fresh, total: open + fresh } };
}

let queryClient: QueryClient;

function seedQueryData(key: unknown[], data: unknown) {
  queryClient.setQueryData(key, data);
}

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
) {
  const def = DASHBOARD_WIDGETS.get(key);
  if (!def) throw new Error(`widget ${key} is not registered`);
  const Component = def.component;
  const ui = (
    <WidgetChromeProvider value={chrome}>
      <WidgetDataProvider value={{ ...state, refetch }}>
        <Component
          instanceId="w-test"
          settings={settingsWithDefaults(def, settings, def.version)}
          size={{ w: def.size.default[0], h: def.size.default[1] }}
          filters={{}}
          mode={mode}
        />
      </WidgetDataProvider>
    </WidgetChromeProvider>
  );
  const rootRoute = createRootRoute({ component: () => ui });
  const children = ["/agent/tickets/$ticketId", "/agent/queues", "/agent/calendar", "/agent/searches/$searchId"].map((path) =>
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

describe("registerCoreWidgets", () => {
  it("registers exactly the ten core keys once, owned by core, with the D9 sizes, and is idempotent", () => {
    DASHBOARD_WIDGETS.clearForTests();
    registerCoreWidgets();
    registerCoreWidgets();
    expect(DASHBOARD_WIDGETS.list().map((w) => w.key).sort()).toEqual([...CORE_WIDGET_KEYS].sort());
    expect(CORE_WIDGET_KEYS).toHaveLength(10);
    expect(DASHBOARD_WIDGETS.get("core.counter")!.size).toEqual({ default: [3, 2], min: [2, 2], max: [4, 3] });
    expect(DASHBOARD_WIDGETS.get("core.ticket-list")!.size).toEqual({ default: [6, 5], min: [4, 3], max: [12, 10] });
    expect(DASHBOARD_WIDGETS.get("core.links")!.size).toEqual({ default: [4, 2], min: [2, 2], max: [12, 6] });
    for (const key of CORE_WIDGET_KEYS) expect(DASHBOARD_WIDGETS.owner(key)).toBe("core");
  });

  it("wires the D9 data endpoints, refresh triggers and categories", () => {
    const get = (k: string) => DASHBOARD_WIDGETS.get(k)!;
    expect(get("core.queue-overview").dataEndpoint).toBeNull();
    expect(get("core.recent-views").dataEndpoint).toBeNull();
    expect(get("core.notes").dataEndpoint).toBeNull();
    expect(get("core.links").dataEndpoint).toBeNull();
    for (const k of DATA_WIDGET_KEYS) expect(get(k).dataEndpoint).toBe(k);
    expect(get("core.escalations").refreshOn).toEqual(["ticket_changed", "interval:60"]);
    expect(get("core.team-presence").refreshOn).toEqual(["interval:30"]);
    expect(get("core.reminders").category).toBe("other");
    expect(get("core.notes").category).toBe("team");
    for (const k of CORE_WIDGET_KEYS) expect(get(k).permission).toBeNull();
  });

  it("keeps a key someone else registered first instead of throwing", () => {
    const counter = { ...DASHBOARD_WIDGETS.get("core.counter")!, owner: "org.example" };
    DASHBOARD_WIDGETS.clearForTests();
    DASHBOARD_WIDGETS.register(counter, "org.example");
    expect(() => registerCoreWidgets()).not.toThrow();
    expect(DASHBOARD_WIDGETS.owner("core.counter")).toBe("org.example");
    expect(DASHBOARD_WIDGETS.list()).toHaveLength(10);
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

  it("is not a link (phase 1 has no drill-down) and uses the host's title override as label", async () => {
    await renderWidget("core.counter", preset("mine_new"), { status: "ok", data: { value: 3, delta: 0, spark: [3, 3] } }, "view", {
      titleOverride: "Neu bei mir",
    });
    expect(screen.getByText("Neu bei mir")).toBeVisible();
    expect(screen.getByText("3")).toBeVisible();
    expect(screen.queryByRole("link")).toBeNull();
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

  it("offers 'Alle 12 anzeigen' only when total > rows, linking to the matching queue view", async () => {
    await renderWidget("core.ticket-list", preset("mine_new"), { status: "ok", data: { items: rows(8), total: 12 } });
    const link = screen.getByRole("link", { name: "Alle 12 anzeigen" });
    expect(link.getAttribute("href")).toContain("/agent/queues");
    expect(link.getAttribute("href")).toContain("owner_id=5");
    expect(link.getAttribute("href")).toContain("state_type=new");
    cleanup();
    await renderWidget("core.ticket-list", preset("mine_new"), { status: "ok", data: { items: rows(3), total: 3 } });
    expect(screen.queryByText(/Alle \d+ anzeigen/)).toBeNull();    expect(screen.queryByText(/insgesamt/)).toBeNull();
  });

  it("shows the total as text, not a link, where no queue view lists the same tickets", async () => {
    await renderWidget("core.ticket-list", preset("mine_open"), { status: "ok", data: { items: rows(8), total: 12 } });
    expect(screen.getByText("12 insgesamt")).toBeVisible();
    expect(screen.queryByRole("link", { name: /anzeigen/ })).toBeNull();
    cleanup();
    await renderWidget("core.ticket-list", preset("escalated"), { status: "ok", data: { items: rows(8), total: 9 } });
    expect(screen.getByText("9 insgesamt")).toBeVisible();
  });

  it("'Alle 12 anzeigen' of a saved-search list links to /agent/searches/<id>; an own filter has no link", async () => {
    await renderWidget("core.ticket-list", { source: { kind: "saved_search", id: 9 }, limit: 8 }, { status: "ok", data: { items: rows(8), total: 12 } });
    expect(screen.getByText("Alle 12 anzeigen").closest("a")).toHaveAttribute("href", "/agent/searches/9");
    cleanup();
    await renderWidget("core.ticket-list", { source: { kind: "filter", filter: { version: 1 } }, limit: 8 }, { status: "ok", data: { items: rows(8), total: 12 } });
    expect(screen.queryByText("Alle 12 anzeigen")).toBeNull();
    expect(screen.getByText("12 insgesamt")).toBeVisible();
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

  it("has an empty state", async () => {
    await renderWidget("core.team-presence", {}, { status: "ok", data: { online: [], total_online: 0 } });
    expect(screen.getByText("Gerade ist niemand online.")).toBeVisible();
  });
});

// ---------------------------------------------------------------- queue overview

describe("core.queue-overview", () => {
  it("uses the cached queue tree, hides empty queues when only_with_work, caps at limit", async () => {
    seedQueryData(["queues"], [q(1, "Support", 38, 6), q(2, "Leer", 0, 0), q(3, "Buchhaltung", 9, 2)]);
    await renderWidget("core.queue-overview", { limit: 1, only_with_work: true }, { status: "none" });
    expect(screen.getByText("Support")).toBeVisible();
    expect(screen.getByText("38 offen · 6 neu")).toBeVisible();
    expect(screen.queryByText("Leer")).toBeNull();
    expect(screen.queryByText("Buchhaltung")).toBeNull();
  });

  it("shows empty queues when only_with_work is off, busiest first, including children", async () => {
    const parent = { ...q(1, "Support", 3, 0), children: [q(4, "Support::2nd Level", 7, 1)] };
    seedQueryData(["queues"], [parent, q(2, "Leer", 0, 0)]);
    await renderWidget("core.queue-overview", { limit: 8, only_with_work: false }, { status: "none" });
    const names = screen.getAllByTestId(/^queue-row-\d+$/).map((el) => el.getAttribute("data-testid"));
    expect(names).toEqual(["queue-row-4", "queue-row-1", "queue-row-2"]);
    expect(screen.getByText("2nd Level")).toBeVisible();
  });

  it("says so when no queue has work", async () => {
    seedQueryData(["queues"], [q(2, "Leer", 0, 0)]);
    await renderWidget("core.queue-overview", {}, { status: "none" });
    expect(screen.getByText("Keine Queues mit offenen oder neuen Tickets.")).toBeVisible();
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
