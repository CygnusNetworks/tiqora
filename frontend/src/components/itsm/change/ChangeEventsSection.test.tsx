import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import i18n from "@/i18n";
import type { ChangeEvent } from "@/lib/changeApi";
import { renderWithClient } from "./changeTestSupport";
import { ChangeEventsSection, EVENTS_PAGE } from "./ChangeEventsSection";

const { events, listReferenceAgents } = vi.hoisted(() => ({ events: vi.fn(), listReferenceAgents: vi.fn() }));
vi.mock("@/lib/changeApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/changeApi")>("@/lib/changeApi");
  return { ...actual, changeApi: { ...actual.changeApi, events } };
});

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { ...actual.api, listReferenceAgents } };
});

const ev = (id: number, over: Partial<ChangeEvent> = {}): ChangeEvent => ({
  id,
  created_at: "2026-10-12T08:00:00Z",
  created_by: 4,
  event: "updated",
  from_phase: null,
  to_phase: null,
  detail: null,
  ...over,
});

describe("ChangeEventsSection", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("de");
    events.mockReset();
    listReferenceAgents.mockReset().mockResolvedValue([{ id: 4, login: "ada", full_name: "Ada Agent" }]);
  });
  afterEach(cleanup);

  it("lists the events newest first with phase steps, reasons and who wrote them", async () => {
    events.mockResolvedValue([
      ev(3, { event: "approval_outcome", created_by: 1, detail: { status: "rejected", reason: "no_approvers", round: 2 } }),
      ev(2, { event: "phase", from_phase: "assessment", to_phase: "approval", detail: { reason: "CAB am Dienstag" } }),
      ev(1, { event: "phase", from_phase: null, to_phase: "draft" }),
    ]);
    renderWithClient(<ChangeEventsSection ticketId={130} />);
    const rows = await screen.findAllByTestId("change-event");
    expect(rows.map((r) => r.getAttribute("data-event-id"))).toEqual(["3", "2", "1"]);
    expect(rows[0]).toHaveTextContent("Ergebnis der Freigabe");
    expect(rows[0]).toHaveTextContent("System");
    expect(rows[0]).toHaveTextContent("Keine gültigen CAB-Mitglieder");
    expect(rows[1]).toHaveTextContent("Phase: Bewertung → Freigabe");
    expect(rows[1]).toHaveTextContent("CAB am Dienstag");
    await waitFor(() => expect(rows[1]).toHaveTextContent("Ada Agent"));
    expect(rows[2]).toHaveTextContent("Phase: Entwurf");
    expect(rows[2]).not.toHaveTextContent("–");
    expect(events).toHaveBeenCalledWith(130, { limit: EVENTS_PAGE, before_id: undefined }, expect.anything());
    // a short first page has nothing more
    expect(screen.queryByTestId("change-events-more")).toBeNull();
  });

  it("pages backwards from the oldest event shown", async () => {
    const page = Array.from({ length: EVENTS_PAGE }, (_, i) => ev(200 - i));
    events.mockResolvedValueOnce(page).mockResolvedValueOnce([ev(100)]);
    renderWithClient(<ChangeEventsSection ticketId={130} />);
    fireEvent.click(await screen.findByTestId("change-events-more"));
    await waitFor(() => expect(events).toHaveBeenLastCalledWith(130, { limit: EVENTS_PAGE, before_id: 200 - EVENTS_PAGE + 1 }, expect.anything()));
    await waitFor(() => expect(screen.getAllByTestId("change-event")).toHaveLength(EVENTS_PAGE + 1));
    expect(screen.queryByTestId("change-events-more")).toBeNull();
  });

  it("keeps the user number for an agent the list does not know", async () => {
    events.mockResolvedValue([ev(1, { created_by: 77 })]);
    renderWithClient(<ChangeEventsSection ticketId={130} />);
    const row = await screen.findByTestId("change-event");
    await waitFor(() => expect(listReferenceAgents).toHaveBeenCalled());
    expect(row).toHaveTextContent("Benutzer #77");
  });

  it("reads the template notes of a new change as sentences, not codes", async () => {
    events.mockResolvedValue([
      ev(1, { event: "created", detail: { template_warnings: ["queue_not_found:5", "risk_answer_stale:tested", "ci_refs_truncated"] } }),
    ]);
    renderWithClient(<ChangeEventsSection ticketId={130} />);
    const row = await screen.findByTestId("change-event");
    expect(row).toHaveTextContent("Queue 5 gibt es nicht mehr");
    expect(row).toHaveTextContent("Frage „tested“");
    expect(row).not.toHaveTextContent("queue_not_found");
  });

  it("shows detail values as plain text, never as markup", async () => {
    events.mockResolvedValue([ev(1, { event: "forced", detail: { reason: "<b>dringend</b>", open_tasks: 2 } })]);
    renderWithClient(<ChangeEventsSection ticketId={130} />);
    const row = await screen.findByTestId("change-event");
    expect(row).toHaveTextContent("<b>dringend</b>");
    expect(within(row).queryByText("dringend", { selector: "b" })).toBeNull();
    expect(row).toHaveTextContent("Offene Aufgaben");
  });
});
