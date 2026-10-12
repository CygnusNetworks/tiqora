import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { createDemoSchedules } from "@/demo/schedulesMock";
import { renderSchedulePages } from "@/components/reports/schedules/testUtils";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { reportSchedulesApi } from "@/lib/reportSchedulesApi";
import { setDisplayTimeZone } from "@/lib/timeZone";
import { ReportSchedulesPage } from "./ReportSchedulesPage";

const auth = vi.hoisted(() => ({ user: {} as Record<string, unknown> }));
vi.mock("@/auth/AuthContext", () => ({ useAuth: () => ({ user: auth.user }) }));

const demo = createDemoSchedules();
const RIGHTS = { can_view_reports: true, can_create_reports: true, is_admin: false };

beforeAll(async () => {
  window.scrollTo = vi.fn();
  await i18n.changeLanguage("de");
  setDisplayTimeZone("Europe/Berlin");
});

beforeEach(() => {
  auth.user = { ...RIGHTS };
  vi.spyOn(reportSchedulesApi, "list").mockImplementation(async (all) => demo.list(all ?? false));
});

afterEach(() => vi.restoreAllMocks());

const render = (path = "/agent/reports/schedules") => renderSchedulePages(path, { list: ReportSchedulesPage });

describe("ReportSchedulesPage", () => {
  it("lists schedules with rhythm, next run and the pause reason", async () => {
    render();
    const row1 = await screen.findByTestId("schedule-row-1");
    expect(row1).toHaveTextContent("Wochenübersicht Support");
    expect(row1).toHaveTextContent("Jeden Montag um 07:30 Uhr");
    expect(row1).toHaveTextContent("Versendet");
    const paused = screen.getByTestId("schedule-row-2");
    expect(paused).toHaveTextContent("Angehalten");
    expect(screen.getByTestId("schedule-row-2-reason")).toHaveTextContent("Angehalten: Angehalten nach 3 fehlgeschlagenen");
  });

  it("adds the schedule zone to the rhythm when it differs from the shown zone", async () => {
    const rows = demo.list(false);
    vi.mocked(reportSchedulesApi.list).mockResolvedValue([{ ...rows[0]!, time_zone: "America/New_York" }, ...rows.slice(1)]);
    render();
    expect(await screen.findByTestId("schedule-row-1-recurrence")).toHaveTextContent("Jeden Montag um 07:30 Uhr (America/New_York)");
    expect(screen.getByTestId("schedule-row-2-recurrence")).not.toHaveTextContent("(");
  });

  it("toggles active with a PUT that carries the version of the schedule", async () => {
    const full = demo.get(1)!;
    vi.spyOn(reportSchedulesApi, "get").mockResolvedValue(full);
    const update = vi.spyOn(reportSchedulesApi, "update").mockResolvedValue({ ...full, active: false });
    render();
    const toggle = await screen.findByTestId("schedule-toggle-1");
    expect(toggle).toBeChecked();
    fireEvent.click(toggle);
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    expect(update.mock.calls[0][0]).toBe(1);
    expect(update.mock.calls[0][1]).toMatchObject({ active: false, version: full.version, name: full.name });
  });

  it("says so when the schedule was changed meanwhile (409)", async () => {
    vi.spyOn(reportSchedulesApi, "get").mockResolvedValue(demo.get(1)!);
    vi.spyOn(reportSchedulesApi, "update").mockRejectedValue(new ApiError(409, { detail: { message: "x" } }, "/api/v1/report-schedules/1"));
    render();
    fireEvent.click(await screen.findByTestId("schedule-toggle-1"));
    expect(await screen.findByTestId("schedules-message")).toHaveTextContent(/inzwischen geändert/);
  });

  it("offers 'Alle anzeigen' only to admins", async () => {
    render();
    await screen.findByTestId("schedule-row-1");
    expect(screen.queryByTestId("schedules-show-all")).toBeNull();
  });

  it("lets an admin ask the list for all and shows the owner", async () => {
    auth.user = { ...RIGHTS, is_admin: true };
    render();
    const sw = await screen.findByTestId("schedules-show-all");
    fireEvent.click(sw);
    await waitFor(() => expect(reportSchedulesApi.list).toHaveBeenCalledWith(true, expect.anything()));
    expect(await screen.findByText("Ersteller")).toBeInTheDocument();
  });

  it("shows the empty state with a create link", async () => {
    vi.spyOn(reportSchedulesApi, "list").mockResolvedValue([]);
    render();
    const empty = await screen.findByTestId("schedules-empty");
    expect(empty).toHaveTextContent("Es gibt noch keine Zeitpläne.");
    expect(within(empty).getByRole("link", { name: "Zeitplan anlegen" })).toHaveAttribute("href", "/agent/reports/schedules/new");
  });

  it("shows a notice instead of the page without the create right", async () => {
    auth.user = { ...RIGHTS, can_create_reports: false };
    render();
    expect(await screen.findByTestId("schedules-denied")).toBeInTheDocument();
    expect(reportSchedulesApi.list).not.toHaveBeenCalled();
  });
});
