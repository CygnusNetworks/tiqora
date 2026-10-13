import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import type { MajorIncident } from "@/lib/itsmApi";
import { makeItsm, makeTicket, PERMS } from "./testSupport";
import { MajorIncidentPanel } from "./MajorIncidentPanel";

const { ticketItsm, get, declare, end, postUpdate, jobGet } = vi.hoisted(() => ({
  ticketItsm: vi.fn(),
  get: vi.fn(),
  declare: vi.fn(),
  end: vi.fn(),
  postUpdate: vi.fn(),
  jobGet: vi.fn(),
}));
vi.mock("@/lib/itsmApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/itsmApi")>("@/lib/itsmApi");
  return { ...actual, itsmApi: { ...actual.itsmApi, ticketItsm, majorIncident: { get, declare, end, postUpdate } } };
});
vi.mock("@/lib/jobsApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/jobsApi")>("@/lib/jobsApi");
  return { ...actual, jobsApi: { ...actual.jobsApi, get: jobGet } };
});

function major(over: Partial<MajorIncident> = {}): MajorIncident {
  return {
    ticket_id: 7, active: true, declared_at: "2026-10-11T08:00:00Z", declared_by: 1, ended_at: null, ended_by: null,
    summary: "Portal nicht erreichbar",
    timeline: [
      { kind: "declared", at: "2026-10-11T08:00:00Z", by: 1, summary: "Portal nicht erreichbar", target_count: 0 },
      {
        kind: "update", at: "2026-10-11T08:30:00Z", by: 1, mode: "note", target_count: 3, update_id: 1,
        body: "<p>Ursache gefunden</p>",
      },
    ],
    ...over,
  };
}

function renderPanel(ticket = makeTicket()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <MajorIncidentPanel ticket={ticket} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

async function typeUpdate(text: string) {
  const editor = await screen.findByTestId("major-update-body");
  editor.innerHTML = text;
  fireEvent.input(editor);
}

describe("MajorIncidentPanel", () => {
  beforeEach(() => {
    for (const f of [ticketItsm, get, declare, end, postUpdate, jobGet]) f.mockReset();
  });
  afterEach(cleanup);

  it("renders nothing for tickets that are neither incident nor major incident", async () => {
    ticketItsm.mockResolvedValue(makeItsm({ role: "problem" }));
    renderPanel();
    await waitFor(() => expect(ticketItsm).toHaveBeenCalled());
    expect(screen.queryByTestId("major-panel")).toBeNull();
  });

  it("declares a major incident from an incident with an optional summary", async () => {
    ticketItsm.mockResolvedValue(makeItsm({ role: "incident" }));
    declare.mockResolvedValue(major());
    renderPanel();
    expect(await screen.findByTestId("major-none")).toBeTruthy();
    expect(screen.queryByTestId("major-end")).toBeNull();
    fireEvent.click(screen.getByTestId("major-declare"));
    fireEvent.change(screen.getByTestId("major-declare-summary"), { target: { value: "Strom weg" } });
    fireEvent.click(screen.getByTestId("major-declare-submit"));
    await waitFor(() => expect(declare).toHaveBeenCalledWith(7, "Strom weg"));
  });

  it("shows summary and timeline of an active major incident and ends it after confirmation", async () => {
    ticketItsm.mockResolvedValue(makeItsm({ role: "major_incident", major_incident: major() }));
    get.mockResolvedValue(major());
    end.mockResolvedValue(major({ active: false }));
    renderPanel();
    expect((await screen.findByTestId("major-summary")).textContent).toContain("Portal nicht erreichbar");
    expect(screen.getByTestId("major-timeline-declared")).toBeTruthy();
    expect(screen.getByTestId("major-timeline-update").textContent).toContain(i18n.t("itsm.major.timeline.targets", { count: 3 }));
    expect(screen.queryByTestId("major-declare")).toBeNull();
    fireEvent.click(screen.getByTestId("major-end"));
    expect(end).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByTestId("confirm-dialog-confirm"));
    await waitFor(() => expect(end).toHaveBeenCalledWith(7));
  });

  it("offers the update modes; the customer reply only when enabled", async () => {
    ticketItsm.mockResolvedValue(makeItsm({ role: "major_incident", major_incident: major() }));
    get.mockResolvedValue(major());
    renderPanel();
    const select = (await screen.findByTestId("major-update-mode")) as HTMLSelectElement;
    expect([...select.options].map((o) => o.value)).toEqual(["internal", "note"]);
    cleanup();
    ticketItsm.mockResolvedValue(
      makeItsm({ role: "major_incident", major_incident: major(), major_incident_customer_reply: true }),
    );
    renderPanel();
    const select2 = (await screen.findByTestId("major-update-mode")) as HTMLSelectElement;
    expect([...select2.options].map((o) => o.value)).toEqual(["internal", "note", "reply"]);
  });

  it("posts an update to all linked tickets and follows the job", async () => {
    ticketItsm.mockResolvedValue(makeItsm({ role: "major_incident", major_incident: major() }));
    get.mockResolvedValue(major());
    postUpdate.mockResolvedValue({ update: major().timeline[1], job_id: 12 });
    jobGet.mockResolvedValue({
      id: 12, status: "running", progress_done: 1, progress_total: 3, progress_message: null,
      cancel_requested: false, error: null, files: [], result: null,
    });
    renderPanel();
    await screen.findByTestId("major-update-form");
    fireEvent.change(screen.getByTestId("major-update-mode"), { target: { value: "note" } });
    const submit = screen.getByTestId("major-update-submit") as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    await typeUpdate("Neuer Stand");
    await waitFor(() => expect(submit.disabled).toBe(false));
    fireEvent.click(submit);
    await waitFor(() => expect(postUpdate).toHaveBeenCalledWith(7, { mode: "note", body_html: "Neuer Stand" }));
    expect(await screen.findByTestId("major-update-job")).toBeTruthy();
  });

  it("an ended incident has no update form; without rw there are no controls at all", async () => {
    ticketItsm.mockResolvedValue(makeItsm({ role: "major_incident", major_incident: major({ active: false }) }));
    get.mockResolvedValue(major({ active: false }));
    renderPanel();
    await screen.findByTestId("major-summary");
    expect(screen.queryByTestId("major-update-form")).toBeNull();
    expect(screen.queryByTestId("major-end")).toBeNull();
    // ended: it can be declared again (the backend starts a new run)
    expect(screen.getByTestId("major-declare")).toBeTruthy();
    cleanup();
    ticketItsm.mockResolvedValue(makeItsm({ role: "major_incident", major_incident: major() }));
    get.mockResolvedValue(major());
    renderPanel(makeTicket({ permissions: { ...PERMS, rw: false } }));
    await screen.findByTestId("major-timeline");
    expect(screen.queryByTestId("major-update-form")).toBeNull();
    expect(screen.queryByTestId("major-end")).toBeNull();
  });
});
