import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { resolveChangeData } from "@/demo/changeMock";
import { ApiError } from "@/lib/api";
import type { FreezeWindow } from "@/lib/changeAdminApi";
import { bodyOf, serveChangeAdmin, useGerman, type Recorded } from "@/routes/admin/itsm/changeAdminTestUtils";
import { renderWithClient } from "./changeTestSupport";
import { FreezeWindowDialog } from "./FreezeWindowDialog";

beforeAll(useGerman);
afterEach(() => vi.restoreAllMocks());

const BASE = "/api/v1/admin/itsm/change/freeze-windows";
const demoWindow = (id: number) =>
  structuredClone((resolveChangeData(BASE, "GET", "", "tiqora") as FreezeWindow[]).find((w) => w.id === id)!);

const type = (testId: string, value: string) => fireEvent.change(screen.getByTestId(testId), { target: { value } });

describe("FreezeWindowDialog", () => {
  it("creates a weekly window for one service, times in UTC", async () => {
    const requests: Recorded[] = [];
    serveChangeAdmin(requests);
    const onClose = vi.fn();
    renderWithClient(<FreezeWindowDialog window={null} onClose={onClose} />);
    expect(screen.getByTestId("freeze-dialog")).toHaveTextContent("Zeiten in UTC");
    type("freeze-name", "Jahresabschluss");
    type("freeze-note", "Kein Eingriff am Kundenportal.");
    fireEvent.click(screen.getByTestId("freeze-enforcement-warn"));
    fireEvent.click(screen.getByTestId("freeze-scope-services"));
    fireEvent.click(screen.getByTestId("freeze-service-add"));
    fireEvent.click(await screen.findByTestId("freeze-service-add-menu-option-1"));
    type("freeze-start", "2026-12-28T00:00");
    type("freeze-end", "2027-01-02T00:00");
    fireEvent.click(screen.getByTestId("freeze-recurrence-type"));
    fireEvent.click(screen.getByTestId("freeze-recurrence-type-menu-option-Weekly"));
    type("freeze-recurrence-interval", "2");
    fireEvent.click(screen.getByTestId("freeze-recurrence-end-count"));
    type("freeze-recurrence-count", "4");
    fireEvent.click(screen.getByTestId("freeze-save"));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(bodyOf(requests, "POST", BASE)).toEqual({
      name: "Jahresabschluss",
      note: "Kein Eingriff am Kundenportal.",
      enforcement: "warn",
      allow_emergency: true,
      scope: "services",
      service_ids: [1],
      starts_at: "2026-12-28T00:00:00Z",
      ends_at: "2027-01-02T00:00:00Z",
      recurrence: { type: "Weekly", interval: 2, count: 4, until: null },
      valid: true,
    });
  });

  it("shows what is missing before it saves", async () => {
    const requests: Recorded[] = [];
    serveChangeAdmin(requests);
    renderWithClient(<FreezeWindowDialog window={null} onClose={vi.fn()} />);
    fireEvent.click(screen.getByTestId("freeze-scope-services"));
    type("freeze-start", "2026-12-28T00:00");
    type("freeze-end", "2026-12-27T00:00");
    fireEvent.click(screen.getByTestId("freeze-save"));
    const problems = await screen.findByTestId("freeze-problems");
    expect(within(problems).getByText("Bitte geben Sie einen Namen ein.")).toBeInTheDocument();
    expect(within(problems).getByText("Das Ende muss nach dem Beginn liegen.")).toBeInTheDocument();
    expect(within(problems).getByText("Bitte wählen Sie mindestens einen Service.")).toBeInTheDocument();
    expect(requests.some((r) => r.method === "POST")).toBe(false);
  });

  it("edits a stored window and keeps its monthly recurrence", async () => {
    const requests: Recorded[] = [];
    serveChangeAdmin(requests);
    const onClose = vi.fn();
    renderWithClient(<FreezeWindowDialog window={demoWindow(1)} onClose={onClose} />);
    expect(screen.getByTestId("freeze-name")).toHaveValue("Quartalsabschluss");
    expect(screen.getByTestId("freeze-start")).toHaveValue("2026-10-13T00:00");
    expect(screen.getByTestId("freeze-recurrence-type")).toHaveTextContent("Monatlich");
    expect(screen.getByTestId("freeze-recurrence-interval")).toHaveValue(3);
    await waitFor(() => expect(screen.getByTestId("freeze-service-chip")).toHaveTextContent("Kundenportal"));
    type("freeze-recurrence-interval", "6");
    fireEvent.click(screen.getByTestId("freeze-recurrence-end-until"));
    type("freeze-recurrence-until", "2027-12-31T00:00");
    fireEvent.click(screen.getByTestId("freeze-save"));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(bodyOf(requests, "PUT", `${BASE}/1`)).toMatchObject({
      name: "Quartalsabschluss",
      enforcement: "block",
      scope: "services",
      service_ids: [1],
      recurrence: { type: "Monthly", interval: 6, count: null, until: "2027-12-31T00:00:00Z" },
    });
  });

  it("sends no services and no recurrence for an all-services one-off window", async () => {
    const requests: Recorded[] = [];
    serveChangeAdmin(requests);
    const onClose = vi.fn();
    renderWithClient(<FreezeWindowDialog window={demoWindow(2)} onClose={onClose} />);
    fireEvent.click(screen.getByTestId("freeze-save"));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(bodyOf(requests, "PUT", `${BASE}/2`)).toMatchObject({ scope: "all", service_ids: [], recurrence: null });
  });

  it("explains a refused save in both error shapes", async () => {
    let shape: "code" | "list" = "code";
    serveChangeAdmin([], (method, path) => {
      if (method !== "PUT" || path !== `${BASE}/1`) return undefined;
      if (shape === "code") throw new ApiError(422, { detail: { code: "freeze_recurrence_invalid" } }, path);
      throw new ApiError(422, { detail: [{ loc: ["body", "ends_at"], msg: "Value error, ends_at must be after starts_at", type: "value_error" }] }, path);
    });
    renderWithClient(<FreezeWindowDialog window={demoWindow(1)} onClose={vi.fn()} />);
    fireEvent.click(screen.getByTestId("freeze-save"));
    expect(await screen.findByTestId("freeze-error")).toHaveTextContent("Das Ende der Wiederholung liegt vor dem Beginn.");
    shape = "list";
    fireEvent.click(screen.getByTestId("freeze-save"));
    await waitFor(() => expect(screen.getByTestId("freeze-error")).toHaveTextContent("Die Eingabe ist ungültig (Feld: ends_at)."));
  });
});
