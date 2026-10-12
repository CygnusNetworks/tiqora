import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { createDemoSchedules } from "@/demo/schedulesMock";
import { demoReport } from "@/demo/reportsMock";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { reportsApi } from "@/lib/reportsApi";
import { reportSchedulesApi } from "@/lib/reportSchedulesApi";
import { setDisplayTimeZone } from "@/lib/timeZone";
import { recurrencePreviewDebounce } from "@/components/reports/schedules/scheduleUi";
import { renderSchedulePages } from "@/components/reports/schedules/testUtils";
import { ReportScheduleEditorPage } from "./ReportScheduleEditorPage";

const auth = vi.hoisted(() => ({
  user: {} as Record<string, unknown>,
}));
vi.mock("@/auth/AuthContext", () => ({ useAuth: () => ({ user: auth.user }) }));

vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  api: {
    listReferenceAgents: vi.fn(async () => [
      { id: 1, login: "alex", full_name: "Alex Turner" },
      { id: 2, login: "erika", full_name: "Erika Beispiel" },
    ]),
  },
}));

const RIGHTS = { can_view_reports: true, can_create_reports: true, can_share_reports: true, report_external_recipients: true, is_admin: false, id: 1 };

function seed() {
  return createDemoSchedules().get(1)!;
}

beforeAll(async () => {
  window.scrollTo = vi.fn();
  await i18n.changeLanguage("de");
  setDisplayTimeZone("Europe/Berlin");
});

beforeEach(() => {
  auth.user = { ...RIGHTS };
  recurrencePreviewDebounce.ms = 10;
  vi.spyOn(reportsApi, "catalogue").mockResolvedValue({
    facts: {} as never,
    reports: ["builtin.volume", "builtin.backlog_trend"].map((k) => demoReport(k)!),
  });
  vi.spyOn(reportsApi, "shareGroups").mockResolvedValue([
    { id: 2, name: "users" },
    { id: 3, name: "Support" },
  ]);
  vi.spyOn(reportSchedulesApi, "runs").mockResolvedValue([]);
  vi.spyOn(reportSchedulesApi, "previewRecurrence").mockResolvedValue({
    text: "Jeden Montag um 07:00 Uhr",
    next_runs: [
      "2026-10-19T05:00:00Z",
      "2026-10-26T06:00:00Z",
      "2026-11-02T06:00:00Z",
      "2026-11-09T06:00:00Z",
      "2026-11-16T06:00:00Z",
    ],
  });
});

afterEach(() => {
  recurrencePreviewDebounce.ms = 400;
  vi.restoreAllMocks();
});

const render = (path: string) => renderSchedulePages(path, { editor: ReportScheduleEditorPage });

describe("ReportScheduleEditorPage", () => {
  it("creates a schedule from ?report=, saves it with POST and opens it", async () => {
    const created = { ...seed(), id: 11 };
    const create = vi.spyOn(reportSchedulesApi, "create").mockResolvedValue(created);
    vi.spyOn(reportSchedulesApi, "get").mockResolvedValue(created);
    const { router } = render("/agent/reports/schedules/new?report=builtin.volume");
    fireEvent.change(await screen.findByTestId("schedule-name"), { target: { value: "Montagsbericht" } });
    fireEvent.click(screen.getByTestId("schedule-save"));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0][0]).toMatchObject({
      name: "Montagsbericht",
      items: [{ report_key: "builtin.volume", overrides: {} }],
      recurrence: { kind: "weekly", time: "07:00", weekdays: [1] },
      recipients: { user_ids: [1], group_ids: [], role_ids: [], emails: [] },
      time_zone: "Europe/Berlin",
    });
    expect(create.mock.calls[0][0]).not.toHaveProperty("version");
    await waitFor(() => expect(router.state.location.pathname).toBe("/agent/reports/schedules/11"));
  });

  it("does not send an invalid form and names the problems", async () => {
    const create = vi.spyOn(reportSchedulesApi, "create");
    render("/agent/reports/schedules/new");
    fireEvent.click(await screen.findByTestId("schedule-save"));
    expect(create).not.toHaveBeenCalled();
    expect(await screen.findByText("Bitte geben Sie einen Namen ein.")).toBeInTheDocument();
    expect(screen.getByText("Bitte wählen Sie mindestens einen Bericht.")).toBeInTheDocument();
  });

  it("shows the next five dates of the recurrence", async () => {
    render("/agent/reports/schedules/new?report=builtin.volume");
    const list = await screen.findByTestId("recurrence-next-runs");
    expect(within(list).getAllByRole("listitem")).toHaveLength(5);
    expect(screen.getByTestId("recurrence-text")).toHaveTextContent("Jeden Montag um 07:00 Uhr");
    // 2026-10-19T05:00Z is 07:00 in Berlin (CEST)
    expect(within(list).getAllByRole("listitem")[0]).toHaveTextContent("07:00");
  });

  it("updates with the loaded version and shows the conflict with a reload button", async () => {
    const current = seed();
    const get = vi.spyOn(reportSchedulesApi, "get").mockResolvedValue(current);
    const update = vi
      .spyOn(reportSchedulesApi, "update")
      .mockRejectedValueOnce(new ApiError(409, { detail: { message: "x", current_version: 4 } }, "/api/v1/report-schedules/1"));
    render("/agent/reports/schedules/1");
    fireEvent.change(await screen.findByTestId("schedule-name"), { target: { value: "Neuer Name" } });
    fireEvent.click(screen.getByTestId("schedule-save"));
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    expect(update.mock.calls[0][0]).toBe(1);
    expect(update.mock.calls[0][1]).toMatchObject({ name: "Neuer Name", version: current.version });
    const conflict = await screen.findByTestId("schedule-conflict");
    expect(conflict).toHaveTextContent(/inzwischen geändert/);
    get.mockResolvedValue({ ...current, name: "Von anderen geändert", version: current.version + 1 });
    fireEvent.click(within(conflict).getByTestId("schedule-reload"));
    await waitFor(() => expect(screen.getByTestId("schedule-name")).toHaveValue("Von anderen geändert"));
    expect(screen.queryByTestId("schedule-conflict")).toBeNull();
  });

  it("shows the success notice after an update although the refetch brings a new version", async () => {
    const current = seed();
    const saved = { ...current, name: "Neuer Name", version: current.version + 1 };
    const get = vi.spyOn(reportSchedulesApi, "get").mockResolvedValue(current);
    const update = vi.spyOn(reportSchedulesApi, "update").mockImplementation(async () => {
      get.mockResolvedValue(saved);
      return saved;
    });
    render("/agent/reports/schedules/1");
    fireEvent.change(await screen.findByTestId("schedule-name"), { target: { value: "Neuer Name" } });
    fireEvent.click(screen.getByTestId("schedule-save"));
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(get.mock.calls.length).toBeGreaterThan(1));
    expect(await screen.findByTestId("schedule-message")).toHaveTextContent("Der Zeitplan wurde gespeichert.");
    expect(screen.getByTestId("schedule-name")).toHaveValue("Neuer Name");
    // the next save sends the new version
    fireEvent.click(screen.getByTestId("schedule-save"));
    await waitFor(() => expect(update).toHaveBeenCalledTimes(2));
    expect(update.mock.calls[1][1]).toMatchObject({ version: saved.version });
  });

  it("says not found for a schedule id that is no number", async () => {
    const get = vi.spyOn(reportSchedulesApi, "get");
    render("/agent/reports/schedules/abc");
    expect(await screen.findByTestId("schedule-missing")).toHaveTextContent("existiert nicht");
    expect(get).not.toHaveBeenCalled();
  });

  it("takes a numeric ?report= typed by hand as the saved report key", async () => {
    render("/agent/reports/schedules/new?report=41");
    await screen.findByTestId("schedule-editor");
    const create = vi.spyOn(reportSchedulesApi, "create").mockResolvedValue({ ...seed(), id: 12 });
    vi.spyOn(reportSchedulesApi, "get").mockResolvedValue({ ...seed(), id: 12 });
    fireEvent.change(screen.getByTestId("schedule-name"), { target: { value: "Bericht 41" } });
    fireEvent.click(screen.getByTestId("schedule-save"));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0][0].items).toEqual([{ report_key: "41", overrides: {} }]);
  });

  it("shows the default subject of the schedule language and previews in that language", async () => {
    render("/agent/reports/schedules/new?report=builtin.volume");
    const subject = await screen.findByTestId("schedule-subject");
    expect(subject).toHaveAttribute("placeholder", "Bericht: {{schedule}} ({{period}})");
    const preview = vi.mocked(reportSchedulesApi.previewRecurrence);
    await waitFor(() => expect(preview).toHaveBeenCalled());
    expect(preview.mock.calls.at(-1)![2]).toBe("de");
    fireEvent.click(within(screen.getByTestId("schedule-language")).getByText("English"));
    expect(screen.getByTestId("schedule-subject")).toHaveAttribute("placeholder", "Report: {{schedule}} ({{period}})");
    await waitFor(() => expect(preview.mock.calls.at(-1)![2]).toBe("en"));
  });

  it("hides the e-mail input when external recipients are switched off", async () => {
    auth.user = { ...RIGHTS, report_external_recipients: false };
    render("/agent/reports/schedules/new?report=builtin.volume");
    await screen.findByTestId("recipients-fields");
    expect(screen.queryByTestId("recipients-email-input")).toBeNull();
    expect(screen.queryByTestId("recipients-emails")).toBeNull();
  });

  it("offers the e-mail input and adds a valid address as a chip", async () => {
    render("/agent/reports/schedules/new?report=builtin.volume");
    const input = await screen.findByTestId("recipients-email-input");
    fireEvent.change(input, { target: { value: "extern@example.org" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(await screen.findByText("extern@example.org")).toBeInTheDocument();
    fireEvent.change(input, { target: { value: "kaputt" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(await screen.findByText("Bitte geben Sie gültige E-Mail-Adressen ein.")).toBeInTheDocument();
  });

  it("limits recipients to me without the share right and says why", async () => {
    auth.user = { ...RIGHTS, can_share_reports: false };
    render("/agent/reports/schedules/new?report=builtin.volume");
    expect(await screen.findByTestId("recipients-me")).toHaveTextContent("An mich");
    expect(screen.getByTestId("recipients-hint")).toHaveTextContent("Zum Versand an andere fehlt die Berechtigung.");
    expect(screen.queryByTestId("recipients-users")).toBeNull();
    expect(screen.queryByTestId("recipients-email-input")).toBeNull();
  });

  it("shows the paused reason and resumes by saving the schedule active", async () => {
    const paused = { ...createDemoSchedules().get(2)! };
    vi.spyOn(reportSchedulesApi, "get").mockResolvedValue(paused);
    const update = vi.spyOn(reportSchedulesApi, "update").mockResolvedValue({ ...paused, active: true });
    render("/agent/reports/schedules/2");
    expect(await screen.findByTestId("schedule-paused")).toHaveTextContent(paused.paused_reason!);
    fireEvent.click(screen.getByTestId("schedule-resume"));
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    expect(update.mock.calls[0][1]).toMatchObject({ active: true, version: paused.version });
  });

  it("shows a plain notice without the create right", async () => {
    auth.user = { ...RIGHTS, can_create_reports: false };
    render("/agent/reports/schedules/new");
    expect(await screen.findByTestId("schedules-denied")).toBeInTheDocument();
    expect(screen.queryByTestId("schedule-editor")).toBeNull();
  });

  it("deletes after confirmation and returns to the list", async () => {
    vi.spyOn(reportSchedulesApi, "get").mockResolvedValue(seed());
    const remove = vi.spyOn(reportSchedulesApi, "remove").mockResolvedValue(undefined);
    const { router } = render("/agent/reports/schedules/1");
    fireEvent.click(await screen.findByTestId("schedule-delete"));
    fireEvent.click(await screen.findByTestId("confirm-dialog-confirm"));
    await waitFor(() => expect(remove).toHaveBeenCalledWith(1));
    await waitFor(() => expect(router.state.location.pathname).toBe("/agent/reports/schedules"));
  });
});
