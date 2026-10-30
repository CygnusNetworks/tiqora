import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import type { ChangeListItem, ChangeTaskListItem } from "@/lib/changeApi";
import { ChangeListPage } from "./ChangeListPage";

const { list, status, myTasks, listReferenceServices, navigate } = vi.hoisted(() => ({
  list: vi.fn(),
  status: vi.fn(),
  myTasks: vi.fn(),
  listReferenceServices: vi.fn(),
  navigate: vi.fn(),
}));
vi.mock("@tanstack/react-router", async () => {
  const actual = await vi.importActual<typeof import("@tanstack/react-router")>("@tanstack/react-router");
  return { ...actual, useNavigate: () => navigate };
});
vi.mock("@/lib/changeApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/changeApi")>("@/lib/changeApi");
  return { ...actual, changeApi: { ...actual.changeApi, list, status, myTasks } };
});
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { ...actual.api, listReferenceServices } };
});
// The dialog has its own tests; here it only reports "created".
vi.mock("@/components/itsm/change/CreateChangeDialog", () => ({
  CreateChangeDialog: ({ onCreated }: { onCreated: (id: number) => void }) => (
    <button type="button" data-testid="create-dialog-stub" onClick={() => onCreated(140)}>
      stub
    </button>
  ),
}));

const item = (id: number, over: Partial<ChangeListItem> = {}): ChangeListItem => ({
  ticket_id: id,
  tn: `2026101${id}`,
  title: `Change ${id}`,
  queue_id: 1,
  change_type: "normal",
  phase: "assessment",
  outcome: null,
  owner_id: 1,
  planned_start: "2026-10-24T20:00:00Z",
  planned_end: "2026-10-25T00:00:00Z",
  risk_level: "high",
  service_ids: [1, 3],
  updated_at: "2026-10-12T08:00:00Z",
  ...over,
});

const task = (id: number, over: Partial<ChangeTaskListItem> = {}): ChangeTaskListItem => ({
  change_ticket_id: 130,
  change_title: "Change 130",
  change_tn: "20261013130",
  finished: false,
  owner_id: 1,
  planned_start: null,
  planned_end: null,
  position: 1,
  queue_id: 1,
  state: "open",
  state_type: "open",
  task_kind: "workorder",
  ticket_id: id,
  title: `Aufgabe ${id}`,
  tn: `2026101${id}`,
  ...over,
});

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <ChangeListPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const page = (items: ChangeListItem[], total = items.length, truncated = false) => ({ items, total, truncated });
const lastQuery = () => list.mock.calls[list.mock.calls.length - 1][0];

beforeEach(async () => {
  await i18n.changeLanguage("de");
  navigate.mockReset();
  list.mockReset().mockResolvedValue(page([item(130), item(131, { change_type: "standard", phase: "scheduled", risk_level: null })]));
  status.mockReset().mockResolvedValue({ legacy: "none", can_see_legacy: false, available: true });
  myTasks
    .mockReset()
    .mockResolvedValue({ items: [task(141), task(142, { finished: true, state: "closed successful", task_kind: "backout" })], truncated: false });
  listReferenceServices.mockReset().mockResolvedValue([
    { id: 1, name: "E-Mail" },
    { id: 3, name: "VPN" },
  ]);
});
afterEach(cleanup);

describe("ChangeListPage", () => {
  it("lists changes with type, phase, risk, window and service names", async () => {
    renderPage();
    const row = await screen.findByTestId("change-row-130");
    expect(row).toHaveTextContent("Change 130");
    expect(row).toHaveTextContent("Normaler Change");
    expect(row).toHaveTextContent("Bewertung");
    expect(row).toHaveTextContent("Hoch");
    expect(row).toHaveTextContent("2026-10-24 20:00 – 2026-10-25 00:00");
    await waitFor(() => expect(row).toHaveTextContent("E-Mail, VPN"));
    expect(row.querySelector("a")).toHaveAttribute("href", "/agent/itsm/changes/130");
    expect(screen.getByTestId("change-row-131")).toHaveTextContent("Nicht bewertet");
    expect(screen.getByTestId("change-list-range")).toHaveTextContent("1–2 von 2");
    expect(list).toHaveBeenCalledWith(expect.objectContaining({ phase: undefined, mine: undefined, limit: 25, offset: 0 }), expect.anything());
  });

  it("has the four tabs and each one asks for its own data", async () => {
    renderPage();
    await screen.findByTestId("change-row-130");
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Alle", "Meine Changes", "Meine Aufgaben", "Freigabe"]);

    fireEvent.click(screen.getByRole("tab", { name: "Meine Changes" }));
    await waitFor(() => expect(lastQuery()).toMatchObject({ mine: "owned" }));

    fireEvent.click(screen.getByRole("tab", { name: "Freigabe" }));
    await waitFor(() => expect(lastQuery()).toMatchObject({ phase: ["approval"], mine: undefined }));
    expect(screen.queryByTestId("change-list-phase")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "Meine Aufgaben" }));
    expect(await screen.findByTestId("change-task-row-141")).toHaveTextContent("Aufgabe 141");
    expect(screen.getByTestId("change-task-row-141")).toHaveTextContent("Arbeitsauftrag");
    expect(screen.getByTestId("change-task-row-141").querySelectorAll("a")[1]).toHaveAttribute("href", "/agent/itsm/changes/130");
    expect(myTasks).toHaveBeenCalledWith(true, expect.anything());
    fireEvent.click(screen.getByTestId("change-list-open-only"));
    await waitFor(() => expect(myTasks).toHaveBeenCalledWith(false, expect.anything()));
  });

  it("sends the phase, type, service and window filters (window as UTC days)", async () => {
    renderPage();
    await screen.findByTestId("change-row-130");
    fireEvent.click(screen.getByTestId("change-list-phase"));
    fireEvent.click(await screen.findByTestId("change-list-phase-menu-option-scheduled"));
    await waitFor(() => expect(lastQuery()).toMatchObject({ phase: ["scheduled"] }));
    fireEvent.click(screen.getByTestId("change-list-type"));
    fireEvent.click(await screen.findByTestId("change-list-type-menu-option-emergency"));
    await waitFor(() => expect(lastQuery()).toMatchObject({ change_type: ["emergency"] }));
    fireEvent.click(screen.getByTestId("change-list-service"));
    fireEvent.click(await screen.findByTestId("change-list-service-menu-option-3"));
    await waitFor(() => expect(lastQuery()).toMatchObject({ service_id: 3 }));
    fireEvent.change(screen.getByTestId("change-list-from"), { target: { value: "2026-10-20" } });
    fireEvent.change(screen.getByTestId("change-list-to"), { target: { value: "2026-10-26" } });
    await waitFor(() =>
      expect(lastQuery()).toMatchObject({ window_start: "2026-10-20T00:00:00Z", window_end: "2026-10-26T23:59:59Z" }),
    );
  });

  it("debounces the search into one request", async () => {
    renderPage();
    await screen.findByTestId("change-row-130");
    list.mockClear();
    const input = screen.getByTestId("change-list-search");
    fireEvent.change(input, { target: { value: "s" } });
    fireEvent.change(input, { target: { value: "switch" } });
    await waitFor(() => expect(list).toHaveBeenCalledWith(expect.objectContaining({ q: "switch" }), expect.anything()));
    expect(list.mock.calls.filter((c) => c[0].q === "s")).toHaveLength(0);
  });

  it("shows the truncation hint exactly when the list says truncated", async () => {
    renderPage();
    await screen.findByTestId("change-row-130");
    expect(screen.queryByTestId("change-list-truncated")).toBeNull();
    cleanup();
    list.mockResolvedValue(page([item(130)], 5000, true));
    renderPage();
    expect(await screen.findByTestId("change-list-truncated")).toHaveTextContent("unvollständig");
    expect(screen.getByTestId("change-list-range")).toHaveTextContent("mindestens 5000 Treffer");
  });

  it("does not show the rows of the previous tab while the next one loads", async () => {
    renderPage();
    await screen.findByTestId("change-row-130");
    list.mockImplementation(() => new Promise(() => {}));
    fireEvent.click(screen.getByRole("tab", { name: "Freigabe" }));
    await waitFor(() => expect(lastQuery()).toMatchObject({ phase: ["approval"] }));
    expect(screen.queryByTestId("change-row-130")).toBeNull();
  });

  it("keeps the rows while paging", async () => {
    list.mockResolvedValue(page([item(130)], 60));
    renderPage();
    await screen.findByTestId("change-row-130");
    list.mockImplementation(() => new Promise(() => {}));
    fireEvent.click(screen.getByTestId("change-list-next"));
    await waitFor(() => expect(lastQuery()).toMatchObject({ offset: 25 }));
    expect(screen.getByTestId("change-row-130")).toBeInTheDocument();
  });

  it("warns when the window starts after it ends", async () => {
    renderPage();
    await screen.findByTestId("change-row-130");
    fireEvent.change(screen.getByTestId("change-list-from"), { target: { value: "2026-10-26" } });
    fireEvent.change(screen.getByTestId("change-list-to"), { target: { value: "2026-10-20" } });
    expect(await screen.findByTestId("change-list-window-invalid")).toHaveTextContent("nicht vor dem Beginn");
  });

  it("shows the empty and the error state of my tasks", async () => {
    myTasks.mockResolvedValueOnce({ items: [], truncated: false });
    renderPage();
    fireEvent.click(await screen.findByRole("tab", { name: "Meine Aufgaben" }));
    expect(await screen.findByTestId("change-tasks-empty")).toHaveTextContent("Keine Aufgaben gefunden.");
    myTasks.mockRejectedValue(new ApiError(409, { detail: { code: "change_mode_a" } }, "x"));
    fireEvent.click(screen.getByTestId("change-list-open-only"));
    expect(await screen.findByTestId("change-list-error")).toHaveAttribute("data-code", "change_mode_a");
  });

  it("says when only the first tasks are shown", async () => {
    myTasks.mockResolvedValue({ items: [task(141)], truncated: true });
    renderPage();
    fireEvent.click(await screen.findByRole("tab", { name: "Meine Aufgaben" }));
    expect(await screen.findByTestId("change-tasks-truncated")).toHaveTextContent("Es werden nur die ersten 100 Aufgaben angezeigt.");
  });

  it("links the calendar and the CAB meetings, and the archive only for an agent who may see it", async () => {
    renderPage();
    await screen.findByTestId("change-row-130");
    expect(screen.getByTestId("change-link-calendar")).toHaveAttribute("href", "/agent/itsm/changes/calendar");
    expect(screen.getByTestId("change-link-meetings")).toHaveAttribute("href", "/agent/itsm/cab-meetings");
    expect(screen.queryByTestId("change-link-archive")).toBeNull();
    cleanup();
    status.mockResolvedValue({ legacy: "archive", can_see_legacy: true, available: true });
    renderPage();
    expect(await screen.findByTestId("change-link-archive")).toHaveAttribute("href", "/agent/itsm/legacy-changes");
    expect(screen.getByTestId("change-link-archive")).toHaveTextContent("Changes (Znuny-Archiv)");
    cleanup();
    status.mockResolvedValue({ legacy: "archive", can_see_legacy: false, available: true });
    renderPage();
    await screen.findByTestId("change-link-calendar");
    await waitFor(() => expect(status).toHaveBeenCalledTimes(3));
    expect(screen.queryByTestId("change-link-archive")).toBeNull();
  });

  it("pages through the list", async () => {
    list.mockResolvedValue(page([item(130)], 60));
    renderPage();
    await screen.findByTestId("change-row-130");
    expect(screen.getByTestId("change-list-prev")).toBeDisabled();
    fireEvent.click(screen.getByTestId("change-list-next"));
    await waitFor(() => expect(lastQuery()).toMatchObject({ offset: 25 }));
  });

  it("says so for an empty list", async () => {
    list.mockResolvedValue(page([]));
    renderPage();
    expect(await screen.findByTestId("change-list-empty")).toHaveTextContent("Keine Changes gefunden.");
  });

  it("explains Mode A with a link to the Znuny changes", async () => {
    list.mockRejectedValue(new ApiError(409, { detail: { code: "change_mode_a" } }, "x"));
    renderPage();
    const error = await screen.findByTestId("change-list-error");
    expect(error).toHaveTextContent("im Modus A nicht verfügbar");
    expect(error.querySelector("a")).toBeNull();
  });

  it("links the Znuny changes in Mode A only for an agent who may see them", async () => {
    status.mockResolvedValue({ legacy: "live", can_see_legacy: true, available: false });
    list.mockRejectedValue(new ApiError(409, { detail: { code: "change_mode_a" } }, "x"));
    renderPage();
    const error = await screen.findByTestId("change-list-error");
    await waitFor(() => expect(error.querySelector("a")).toHaveAttribute("href", "/agent/itsm/legacy-changes"));
  });

  it("opens the new change after the dialog reports it", async () => {
    renderPage();
    await screen.findByTestId("change-row-130");
    fireEvent.click(screen.getByTestId("change-list-create"));
    fireEvent.click(await screen.findByTestId("create-dialog-stub"));
    expect(navigate).toHaveBeenCalledWith({ to: "/agent/itsm/changes/$ticketId", params: { ticketId: "140" } });
    expect(screen.queryByTestId("create-dialog-stub")).toBeNull();
  });
});
