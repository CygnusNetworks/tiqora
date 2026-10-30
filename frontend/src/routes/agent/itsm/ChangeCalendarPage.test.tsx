import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import type { CalendarChange, ChangeCalendar, ServiceAvailability } from "@/lib/changeApi";
import { MAX_RANGE_DAYS } from "@/lib/changeCalendar";
import { ChangeCalendarPage } from "./ChangeCalendarPage";

const { calendar, status, calendarServices, listReferenceServices, navigate } = vi.hoisted(() => ({
  calendar: vi.fn(),
  status: vi.fn(),
  calendarServices: vi.fn(),
  listReferenceServices: vi.fn(),
  navigate: vi.fn(),
}));
vi.mock("@tanstack/react-router", async () => {
  const actual = await vi.importActual<typeof import("@tanstack/react-router")>("@tanstack/react-router");
  return { ...actual, useNavigate: () => navigate };
});
vi.mock("@/lib/changeApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/changeApi")>("@/lib/changeApi");
  return { ...actual, changeApi: { ...actual.changeApi, calendar, status, calendarServices } };
});
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { ...actual.api, listReferenceServices } };
});

const NOW = new Date("2026-10-29T10:00:00Z");

const change = (id: number, over: Partial<CalendarChange> = {}): CalendarChange => ({
  ticket_id: id,
  tn: `2026101${id}`,
  title: `Change ${id}`,
  queue_id: 1,
  owner_id: 1,
  change_type: "normal",
  phase: "scheduled",
  outcome: null,
  risk_level: "medium",
  planned_start: "2026-10-30T20:00:00Z",
  planned_end: "2026-10-30T23:00:00Z",
  service_ids: [1],
  updated_at: "2026-10-12T08:00:00Z",
  conflict_severity: null,
  conflict_kinds: [],
  ...over,
});

const cal = (over: Partial<ChangeCalendar> = {}): ChangeCalendar => ({
  start: "2026-09-28T00:00:00Z",
  end: "2026-11-09T00:00:00Z",
  changes: [change(130)],
  tasks: [],
  freezes: [],
  truncated: false,
  freezes_truncated: false,
  ...over,
});

const availability: ServiceAvailability[] = [
  {
    service_id: 1,
    service_name: "E-Mail",
    change_count: 1,
    minutes_affected: 90,
    windows: [
      { kind: "change", ref_id: 130, tn: "20261013130", title: "Change 130", start: "2026-10-29T20:00:00Z", end: "2026-10-29T21:30:00Z", change_type: "normal", phase: "scheduled", direct: true, enforcement: null },
      { kind: "freeze", ref_id: 7, tn: null, title: "Jahresabschluss", start: "2026-10-29T00:00:00Z", end: "2026-10-30T00:00:00Z", change_type: null, phase: null, direct: null, enforcement: "block" },
    ],
  },
  { service_id: 2, service_name: "VPN", change_count: 0, minutes_affected: 0, windows: [] },
];

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <ChangeCalendarPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

beforeEach(async () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
  await i18n.changeLanguage("de");
  calendar.mockReset().mockResolvedValue(cal());
  status.mockReset().mockResolvedValue({ can_see_legacy: true, legacy: "live", available: false });
  calendarServices.mockReset().mockResolvedValue(availability);
  listReferenceServices.mockReset().mockResolvedValue([{ id: 1, name: "E-Mail" }, { id: 2, name: "VPN" }]);
  navigate.mockReset();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("month view", () => {
  it("asks for the 42-day grid in UTC without tasks and draws the change on its day", async () => {
    renderPage();
    const bar = await screen.findByTestId("calendar-occurrence-130");
    expect(calendar.mock.calls[0][0]).toEqual({ start: "2026-09-28T00:00:00Z", end: "2026-11-09T00:00:00Z", service_id: undefined, include_tasks: false });
    expect(within(screen.getByTestId("calendar-day-2026-10-30")).getByTestId("calendar-occurrence-130")).toBe(bar);
    expect(bar).toHaveTextContent("Change 130");
  });

  it("colours the bar by phase and marks an emergency change", async () => {
    calendar.mockResolvedValue(cal({ changes: [change(131, { phase: "implementing", change_type: "emergency" })] }));
    renderPage();
    const bar = await screen.findByTestId("calendar-occurrence-131");
    expect(bar.className).toContain("bg-green/30");
    expect(bar.className).toContain("border-danger");
  });

  it("repeats a multi-day change on every day it spans", async () => {
    calendar.mockResolvedValue(cal({ changes: [change(132, { planned_start: "2026-10-30T22:00:00Z", planned_end: "2026-11-01T02:00:00Z" })] }));
    renderPage();
    await screen.findAllByTestId("calendar-occurrence-132");
    for (const d of ["2026-10-30", "2026-10-31", "2026-11-01"]) {
      expect(within(screen.getByTestId(`calendar-day-${d}`)).getByTestId("calendar-occurrence-132")).toBeInTheDocument();
    }
    expect(within(screen.getByTestId("calendar-day-2026-11-02")).queryByTestId("calendar-occurrence-132")).toBeNull();
  });

  it("shades blocking and warning freeze days differently", async () => {
    calendar.mockResolvedValue(
      cal({
        freezes: [
          { freeze_id: 7, name: "Jahresabschluss", enforcement: "block", allow_emergency: false, scope: "all", service_ids: [], start: "2026-10-27T00:00:00Z", end: "2026-10-28T00:00:00Z" },
          { freeze_id: 8, name: "Messe", enforcement: "warn", allow_emergency: true, scope: "all", service_ids: [], start: "2026-10-28T00:00:00Z", end: "2026-10-29T00:00:00Z" },
        ],
      }),
    );
    renderPage();
    await screen.findByTestId("change-freeze-day-2026-10-27");
    expect(screen.getByTestId("calendar-day-2026-10-27").className).toContain("bg-danger/10");
    expect(screen.getByTestId("change-freeze-day-2026-10-27")).toHaveAttribute("data-enforcement", "block");
    expect(screen.getByTestId("calendar-day-2026-10-28").className).toContain("bg-escalation/10");
    expect(screen.queryByTestId("change-freeze-day-2026-10-29")).toBeNull();
  });

  it("marks a conflicting change with its severity", async () => {
    calendar.mockResolvedValue(cal({ changes: [change(133, { conflict_severity: "block", conflict_kinds: ["freeze"] })] }));
    renderPage();
    const mark = await screen.findByTestId("change-conflict-133");
    expect(mark).toHaveAttribute("data-severity", "block");
    expect(mark).toHaveAccessibleName("Blockiert");
  });

  it("opens the change on click", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("calendar-occurrence-130"));
    expect(navigate).toHaveBeenCalledWith({ to: "/agent/itsm/changes/130" });
  });

  it("shows the truncation hints for changes and freeze windows", async () => {
    calendar.mockResolvedValue(cal({ truncated: true, freezes_truncated: true }));
    renderPage();
    expect(await screen.findByTestId("change-calendar-truncated")).toBeInTheDocument();
    expect(screen.getByTestId("change-calendar-freezes-truncated")).toBeInTheDocument();
  });

  it("shows the error text and the Znuny link in Mode A", async () => {
    calendar.mockRejectedValue(new ApiError(409, { detail: { code: "change_mode_a" } }, "/x"));
    renderPage();
    const err = await screen.findByTestId("change-calendar-error");
    expect(err).toHaveAttribute("data-code", "change_mode_a");
    expect(await within(err).findByRole("link")).toHaveAttribute("href", "/agent/itsm/legacy-changes");
  });

  it("shows no Znuny link to an agent who may not see the Znuny changes", async () => {
    status.mockResolvedValue({ can_see_legacy: false, legacy: "live", available: false });
    calendar.mockRejectedValue(new ApiError(409, { detail: { code: "change_mode_a" } }, "/x"));
    renderPage();
    const err = await screen.findByTestId("change-calendar-error");
    await waitFor(() => expect(status).toHaveBeenCalled());
    expect(within(err).queryByRole("link")).toBeNull();
  });

  it("links the list and the CAB meetings in the header, and the archive when the agent may read it", async () => {
    status.mockResolvedValue({ can_see_legacy: true, legacy: "archive", available: true });
    renderPage();
    expect(await screen.findByTestId("change-link-archive")).toHaveAttribute("href", "/agent/itsm/legacy-changes");
    expect(screen.getByTestId("change-link-list")).toHaveAttribute("href", "/agent/itsm/changes");
    expect(screen.getByTestId("change-link-meetings")).toHaveAttribute("href", "/agent/itsm/cab-meetings");
    expect(screen.queryByTestId("change-link-calendar")).toBeNull();
  });
});

describe("navigation and filter", () => {
  it("steps by month and never requests more than 92 days", async () => {
    renderPage();
    await screen.findByTestId("calendar-occurrence-130");
    for (let i = 0; i < 6; i += 1) {
      fireEvent.click(screen.getByTestId("change-calendar-next"));
      await waitFor(() => expect(calendar).toHaveBeenCalledTimes(i + 2));
    }
    expect(screen.getByTestId("change-calendar-label")).toHaveTextContent("April 2027");
    for (const [query] of calendar.mock.calls) {
      expect((Date.parse(query.end) - Date.parse(query.start)) / 86_400_000).toBeLessThanOrEqual(MAX_RANGE_DAYS);
    }
  });

  it("sends the service filter but keeps every freeze shaded, as the backend marks conflicts by all services", async () => {
    calendar.mockResolvedValue(
      cal({
        freezes: [{ freeze_id: 9, name: "Nur VPN", enforcement: "block", allow_emergency: false, scope: "services", service_ids: [2], start: "2026-10-27T00:00:00Z", end: "2026-10-28T00:00:00Z" }],
      }),
    );
    renderPage();
    await screen.findByTestId("change-freeze-day-2026-10-27");
    fireEvent.click(screen.getByTestId("change-calendar-service"));
    fireEvent.click(await screen.findByTestId("change-calendar-service-menu-option-1"));
    await waitFor(() => expect(calendar.mock.calls.at(-1)?.[0].service_id).toEqual([1]));
    expect(screen.getByTestId("change-freeze-day-2026-10-27")).toHaveAttribute("data-enforcement", "block");
  });

  it("shows the loading state, not the old data, when the view changes", async () => {
    renderPage();
    await screen.findByTestId("calendar-occurrence-130");
    let release: (v: ChangeCalendar) => void = () => {};
    calendar.mockReturnValueOnce(new Promise<ChangeCalendar>((r) => (release = r)));
    fireEvent.click(screen.getByRole("tab", { name: "Woche" }));
    await waitFor(() => expect(calendar).toHaveBeenCalledTimes(2));
    expect(screen.queryByTestId("change-week-row-130")).toBeNull();
    expect(screen.queryByTestId("calendar-month-grid")).toBeNull();
    release(cal());
    expect(await screen.findByTestId("change-week-row-130")).toBeInTheDocument();
  });

  it("keeps the old grid while stepping within the same view", async () => {
    renderPage();
    await screen.findByTestId("calendar-occurrence-130");
    calendar.mockReturnValueOnce(new Promise(() => {}));
    fireEvent.click(screen.getByTestId("change-calendar-next"));
    await waitFor(() => expect(calendar).toHaveBeenCalledTimes(2));
    expect(screen.getByTestId("calendar-month-grid")).toBeInTheDocument();
  });

  it("opens the week list of a day from '+n more' and from the day cell", async () => {
    const many = [1, 2, 3, 4, 5].map((n) => change(140 + n, { title: `Viele ${n}` }));
    calendar.mockResolvedValue(cal({ changes: many }));
    renderPage();
    fireEvent.click(await screen.findByTestId("calendar-more-2026-10-30"));
    expect(await screen.findByTestId("change-week-day-2026-10-30")).toBeInTheDocument();
    expect(calendar.mock.calls.at(-1)?.[0]).toMatchObject({ start: "2026-10-26T00:00:00Z", end: "2026-11-02T00:00:00Z" });
    expect(within(screen.getByTestId("change-week-day-2026-10-30")).getAllByTestId(/^change-week-row-/)).toHaveLength(5);
  });
});

describe("week list", () => {
  it("lists the week day by day with changes, freeze badges and links", async () => {
    calendar.mockResolvedValue(
      cal({
        changes: [change(130, { conflict_severity: "warn", conflict_kinds: ["freeze"] })],
        freezes: [{ freeze_id: 7, name: "Jahresabschluss", enforcement: "block", allow_emergency: false, scope: "all", service_ids: [], start: "2026-10-30T00:00:00Z", end: "2026-10-31T00:00:00Z" }],
      }),
    );
    renderPage();
    await screen.findByTestId("calendar-occurrence-130");
    fireEvent.click(screen.getByRole("tab", { name: "Woche" }));
    const day = await screen.findByTestId("change-week-day-2026-10-30");
    expect(calendar.mock.calls.at(-1)?.[0]).toMatchObject({ start: "2026-10-26T00:00:00Z", end: "2026-11-02T00:00:00Z" });
    const row = within(day).getByTestId("change-week-row-130");
    expect(within(row).getByRole("link")).toHaveAttribute("href", "/agent/itsm/changes/130");
    expect(row).toHaveTextContent("Geplant");
    expect(within(row).getByTestId("calendar-conflict-mark")).toHaveAttribute("data-severity", "warn");
    expect(within(day).getByTestId("change-week-freeze-7")).toHaveAttribute("data-enforcement", "block");
    expect(within(screen.getByTestId("change-week-day-2026-10-27")).getByText("Keine Changes geplant.")).toBeInTheDocument();
  });
});

describe("Nach Service", () => {
  async function openServices() {
    renderPage();
    await screen.findByTestId("calendar-occurrence-130");
    fireEvent.click(screen.getByRole("tab", { name: "Nach Service" }));
    await screen.findByTestId(/^(change-calendar-services|change-service-empty)$/);
  }

  it("asks for two weeks and lists services with counts and affected time", async () => {
    await openServices();
    expect(calendarServices).toHaveBeenCalledWith("2026-10-26T00:00:00Z", "2026-11-09T00:00:00Z", expect.anything());
    const row = screen.getByTestId("change-service-row-1");
    expect(row).toHaveTextContent("E-Mail");
    expect(row).toHaveTextContent("1 Std. 30 Min.");
    expect(screen.getByTestId("change-service-cell-1-2026-10-29")).toHaveAttribute("data-changes", "1");
    expect(screen.getByTestId("change-service-cell-1-2026-10-28")).toBeDisabled();
  });

  it("shades a freeze day and marks the change inside it", async () => {
    await openServices();
    const cell = screen.getByTestId("change-service-cell-1-2026-10-29");
    expect(cell.parentElement).toHaveAttribute("data-freeze", "block");
    expect(cell.parentElement?.className).toContain("bg-danger/15");
    expect(within(cell).getByTestId("change-service-conflict")).toHaveAttribute("data-severity", "block");
  });

  it("lists the windows of a clicked cell with a link to the change", async () => {
    await openServices();
    fireEvent.click(screen.getByTestId("change-service-cell-1-2026-10-29"));
    const detail = screen.getByTestId("change-service-detail");
    expect(within(detail).getByRole("link", { name: "Change 130" })).toHaveAttribute("href", "/agent/itsm/changes/130");
    expect(detail).toHaveTextContent("Jahresabschluss");
  });

  it("filters the rows by the service filter", async () => {
    await openServices();
    fireEvent.click(screen.getByTestId("change-calendar-service"));
    fireEvent.click(await screen.findByTestId("change-calendar-service-menu-option-2"));
    await waitFor(() => expect(screen.queryByTestId("change-service-row-1")).toBeNull());
    expect(screen.getByTestId("change-service-row-2")).toBeInTheDocument();
  });

  it("says so when no service has a window", async () => {
    calendarServices.mockResolvedValue([]);
    await openServices();
    expect(screen.getByTestId("change-service-empty")).toBeInTheDocument();
  });
});
