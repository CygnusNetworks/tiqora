import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, createEvent, fireEvent, screen, waitFor, within } from "@testing-library/react";
import i18n from "@/i18n";
import { changeError, renderWithClient } from "@/components/itsm/change/changeTestSupport";
import type { CabChangeItem, CabMeeting } from "@/lib/changeApi";
import { CabMeetingPage } from "./CabMeetingPage";

const { meeting, status, suggestions, saveAgenda, updateMeeting, params } = vi.hoisted(() => ({
  meeting: vi.fn(),
  status: vi.fn(),
  suggestions: vi.fn(),
  saveAgenda: vi.fn(),
  updateMeeting: vi.fn(),
  params: { meetingId: "1" },
}));
vi.mock("@tanstack/react-router", async () => {
  const actual = await vi.importActual<typeof import("@tanstack/react-router")>("@tanstack/react-router");
  return { ...actual, useParams: () => params };
});
vi.mock("@/lib/changeApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/changeApi")>("@/lib/changeApi");
  return { ...actual, changeApi: { ...actual.changeApi, meeting, status, meetingSuggestions: suggestions, saveAgenda, updateMeeting } };
});

const snapshot: NonNullable<CabMeeting["agenda"][number]["approval"]> = {
  approval_id: 634,
  approval_key: "change_cab",
  status: "pending",
  reason: null,
  round: 1,
  current_stage: 1,
  created_at: "2026-10-12T08:00:00Z",
  decided_at: null,
  votes_total: 3,
  votes_approved: 1,
  votes_rejected: 0,
};

const line = (position: number, id: number, over: Partial<CabMeeting["agenda"][number]> = {}): CabMeeting["agenda"][number] => ({
  position,
  hidden: false,
  change_ticket_id: id,
  tn: `2026101${id}`,
  title: `Change ${id}`,
  change_type: "normal",
  phase: "approval",
  planned_start: "2026-10-24T20:00:00Z",
  planned_end: "2026-10-25T00:00:00Z",
  note: null,
  approval: null,
  ...over,
});

const hiddenLine = (position: number): CabMeeting["agenda"][number] => ({
  position,
  hidden: true,
  change_ticket_id: null,
  tn: null,
  title: null,
  change_type: null,
  phase: null,
  planned_start: null,
  planned_end: null,
  note: null,
  approval: null,
});

const makeMeeting = (over: Partial<CabMeeting> = {}): CabMeeting => ({
  id: 1,
  cab_id: 1,
  cab_name: "CAB Netzwerk",
  title: "CAB Netzwerk, Oktober",
  starts_at: "2026-10-15T09:00:00Z",
  ends_at: "2026-10-15T10:00:00Z",
  location: "Raum 2",
  status: "planned",
  minutes: null,
  agenda_count: 3,
  agenda: [line(1, 130, { approval: snapshot, note: "Rückfallplan besprechen." }), hiddenLine(2), line(3, 133)],
  created_at: "2026-10-01T08:00:00Z",
  created_by: 1,
  updated_at: "2026-10-01T08:00:00Z",
  updated_by: 1,
  ...over,
});

const suggestion = (id: number): CabChangeItem => ({
  ticket_id: id,
  tn: `2026101${id}`,
  title: `Vorschlag ${id}`,
  queue_id: 1,
  owner_id: 1,
  change_type: "normal",
  phase: "approval",
  outcome: null,
  risk_level: null,
  planned_start: null,
  planned_end: null,
  service_ids: [],
  updated_at: "2026-10-12T08:00:00Z",
});

const order = () => screen.getAllByTestId(/^cab-agenda-item-\d+$/).map((e) => e.getAttribute("data-testid")!.replace("cab-agenda-item-", ""));

describe("CabMeetingPage", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("de");
    for (const m of [meeting, status, suggestions, saveAgenda, updateMeeting]) m.mockReset();
    params.meetingId = "1";
    status.mockResolvedValue({ can_manage: true });
    meeting.mockResolvedValue(makeMeeting());
    suggestions.mockResolvedValue([suggestion(140), suggestion(133)]);
  });
  afterEach(cleanup);

  it("shows the agenda with each change's approval status and a link to the change", async () => {
    renderWithClient(<CabMeetingPage />);
    expect(await screen.findByTestId("cab-meeting-header")).toHaveTextContent("CAB Netzwerk, Oktober");
    expect(screen.getByTestId("cab-meeting-status")).toHaveTextContent("Geplant");
    const first = screen.getByTestId("cab-agenda-item-130");
    expect(within(first).getByTestId("change-approval-snapshot")).toHaveAttribute("data-status", "pending");
    expect(first).toHaveTextContent("1 von 3 Zustimmungen");
    expect(first).toHaveTextContent("2026-10-24 20:00 – 2026-10-25 00:00 UTC");
    expect(screen.getByTestId("cab-agenda-link-130")).toHaveAttribute("href", "/agent/itsm/changes/130");
    expect(screen.getByTestId("cab-agenda-item-133")).toHaveTextContent("Noch keine Freigabe angefordert.");
  });

  it("numbers the hidden lines after the visible ones and says their order is fixed", async () => {
    renderWithClient(<CabMeetingPage />);
    const hidden = await screen.findByTestId("cab-agenda-hidden");
    expect(hidden).toHaveTextContent("3.");
    expect(screen.getByTestId("cab-agenda-hidden-hint")).toHaveTextContent("ihre Reihenfolge lässt sich nicht ändern");
  });

  it("announces the new position and keeps the focus on a usable arrow", async () => {
    renderWithClient(<CabMeetingPage />);
    await screen.findByTestId("cab-agenda-item-130");
    screen.getByTestId("cab-agenda-down-130").focus();
    fireEvent.click(screen.getByTestId("cab-agenda-down-130"));
    expect(screen.getByTestId("cab-agenda-live")).toHaveTextContent("Position 2 von 2: Change 130");
    // 130 is last now: its down arrow is disabled, focus moves to the up arrow
    await waitFor(() => expect(screen.getByTestId("cab-agenda-up-130")).toHaveFocus());
  });

  it("marks rows added from the suggestions as not yet saved instead of claiming no approval", async () => {
    renderWithClient(<CabMeetingPage />);
    fireEvent.click(await screen.findByTestId("cab-suggestion-add-140"));
    const row = screen.getByTestId("cab-agenda-item-140");
    expect(within(row).getByTestId("cab-agenda-unsaved")).toHaveTextContent("Freigabestand nach dem Speichern sichtbar");
    expect(row).not.toHaveTextContent("Noch keine Freigabe angefordert.");
  });

  it("names the blocking line when a save is refused", async () => {
    renderWithClient(<CabMeetingPage />);
    fireEvent.click(await screen.findByTestId("cab-suggestion-add-140"));
    saveAgenda.mockRejectedValue(changeError(422, { code: "change_not_open", ticket_id: 140 }));
    fireEvent.click(screen.getByTestId("cab-agenda-save"));
    expect(await screen.findByTestId("cab-agenda-error")).toHaveTextContent("Betroffen ist der Change 2026101140.");
    expect(screen.getByTestId("cab-agenda-item-140")).toHaveAttribute("data-blocked", "true");
    expect(screen.getByTestId("cab-agenda-item-130")).not.toHaveAttribute("data-blocked");
  });

  it("keeps lines without read access as 'Change ohne Leserecht' and says they are kept", async () => {
    renderWithClient(<CabMeetingPage />);
    const hidden = await screen.findByTestId("cab-agenda-hidden");
    expect(hidden).toHaveTextContent("Change ohne Leserecht");
    expect(hidden).not.toHaveTextContent("Change 1");
    expect(screen.getByTestId("cab-agenda-hidden-hint")).toHaveTextContent("bleiben auf der Tagesordnung erhalten");
    // Not editable: no controls on the hidden line, and a save leaves it out of the request (the server keeps it).
    expect(within(hidden).queryByRole("button")).toBeNull();
    fireEvent.click(screen.getByTestId("cab-agenda-down-130"));
    saveAgenda.mockResolvedValue(makeMeeting());
    fireEvent.click(screen.getByTestId("cab-agenda-save"));
    await waitFor(() =>
      expect(saveAgenda).toHaveBeenCalledWith(1, {
        items: [
          { change_ticket_id: 133, note: null },
          { change_ticket_id: 130, note: "Rückfallplan besprechen." },
        ],
      }),
    );
  });

  it("orders the agenda with the arrows and by drag and drop, and saves the new order", async () => {
    renderWithClient(<CabMeetingPage />);
    await screen.findByTestId("cab-agenda-item-130");
    expect(order()).toEqual(["130", "133"]);
    expect(screen.getByTestId("cab-agenda-up-130")).toBeDisabled();
    expect(screen.getByTestId("cab-agenda-down-133")).toBeDisabled();
    expect(screen.getByTestId("cab-agenda-save")).toBeDisabled();
    fireEvent.click(screen.getByTestId("cab-agenda-down-130"));
    expect(order()).toEqual(["133", "130"]);
    // drag 130 (now second) back above 133
    const dt = { setData: vi.fn(), effectAllowed: "" };
    const second = screen.getByTestId("cab-agenda-item-130");
    // only the handle drags, so text in the note field stays selectable
    expect(second).not.toHaveAttribute("draggable");
    const handle = screen.getByTestId("cab-agenda-handle-130");
    expect(handle).toHaveAttribute("draggable", "true");
    fireEvent(handle, Object.assign(createEvent.dragStart(handle), { dataTransfer: dt }));
    const target = screen.getByTestId("cab-agenda-item-133");
    fireEvent(target, Object.assign(createEvent.drop(target), { dataTransfer: dt }));
    expect(order()).toEqual(["130", "133"]);
    fireEvent.click(screen.getByTestId("cab-agenda-down-130"));
    const saved = makeMeeting({ agenda: [line(1, 133), line(2, 130), hiddenLine(3)] });
    saveAgenda.mockResolvedValue(saved);
    meeting.mockResolvedValue(saved); // the list refetch after the save
    fireEvent.click(screen.getByTestId("cab-agenda-save"));
    await waitFor(() => expect(saveAgenda).toHaveBeenCalledTimes(1));
    // saved: the server's order is shown and nothing is left to save
    await waitFor(() => expect(screen.queryByTestId("cab-agenda-reset")).toBeNull());
    expect(order()).toEqual(["133", "130"]);
    expect(screen.getByTestId("cab-agenda-save")).toBeDisabled();
  });

  it("adds a suggestion, edits notes, removes a line, and shows a refused save by its code", async () => {
    renderWithClient(<CabMeetingPage />);
    await screen.findByTestId("cab-agenda-item-130");
    // 133 is already on the agenda: only 140 is offered
    expect(await screen.findByTestId("cab-suggestion-140")).toBeInTheDocument();
    expect(screen.queryByTestId("cab-suggestion-133")).toBeNull();
    fireEvent.click(screen.getByTestId("cab-suggestion-add-140"));
    expect(order()).toEqual(["130", "133", "140"]);
    expect(screen.queryByTestId("cab-suggestion-140")).toBeNull();
    fireEvent.change(screen.getByTestId("cab-agenda-note-140"), { target: { value: " Neu " } });
    fireEvent.click(screen.getByTestId("cab-agenda-remove-133"));
    expect(order()).toEqual(["130", "140"]);
    saveAgenda.mockRejectedValue(changeError(422, { code: "change_not_open", ticket_id: 140 }));
    fireEvent.click(screen.getByTestId("cab-agenda-save"));
    expect(await screen.findByTestId("cab-agenda-error")).toHaveTextContent("nicht mehr auf die Tagesordnung");
    expect(saveAgenda).toHaveBeenCalledWith(1, {
      items: [
        { change_ticket_id: 130, note: "Rückfallplan besprechen." },
        { change_ticket_id: 140, note: "Neu" },
      ],
    });
    fireEvent.click(screen.getByTestId("cab-agenda-reset"));
    expect(order()).toEqual(["130", "133"]);
  });

  it("maps a failed suggestion load by its code, not the backend message", async () => {
    suggestions.mockRejectedValue(changeError(422, { code: "cab_invalid", message: "Backend text" }));
    renderWithClient(<CabMeetingPage />);
    const err = await screen.findByTestId("cab-suggestions-error");
    expect(err).toHaveTextContent("Das CAB ist ungültig.");
    expect(err).not.toHaveTextContent("Backend text");
  });

  it("saves minutes and status together, with the visibility hint", async () => {
    renderWithClient(<CabMeetingPage />);
    expect(await screen.findByTestId("cab-minutes-hint")).toHaveTextContent("Das Protokoll ist für alle Agenten sichtbar.");
    expect(screen.getByTestId("cab-minutes-save")).toBeDisabled();
    fireEvent.change(screen.getByTestId("cab-minutes-input"), { target: { value: "Beschluss: freigegeben." } });
    fireEvent.click(screen.getByTestId("cab-status-select"));
    fireEvent.click(await screen.findByTestId("cab-status-select-menu-option-held"));
    const held = makeMeeting({ status: "held", minutes: "Beschluss: freigegeben." });
    updateMeeting.mockResolvedValue(held);
    meeting.mockResolvedValue(held);
    fireEvent.click(screen.getByTestId("cab-minutes-save"));
    await waitFor(() => expect(updateMeeting).toHaveBeenCalledWith(1, { minutes: "Beschluss: freigegeben.", status: "held" }));
    await waitFor(() => expect(screen.getByTestId("cab-meeting-status")).toHaveTextContent("Durchgeführt"));
    expect(screen.getByTestId("cab-minutes-save")).toBeDisabled();
    // a held meeting's agenda is read only
    expect(screen.queryByTestId("cab-agenda-save")).toBeNull();
    expect(screen.queryByTestId("cab-agenda-up-133")).toBeNull();
  });

  it("drops an unsaved agenda line when the meeting stops being editable", async () => {
    renderWithClient(<CabMeetingPage />);
    await screen.findByTestId("cab-agenda-item-130");
    fireEvent.click(await screen.findByTestId("cab-suggestion-add-140"));
    expect(order()).toEqual(["130", "133", "140"]);
    fireEvent.click(screen.getByTestId("cab-status-select"));
    fireEvent.click(await screen.findByTestId("cab-status-select-menu-option-held"));
    const held = makeMeeting({ status: "held", minutes: "Fertig." });
    updateMeeting.mockResolvedValue(held);
    meeting.mockResolvedValue(held);
    fireEvent.change(screen.getByTestId("cab-minutes-input"), { target: { value: "Fertig." } });
    fireEvent.click(screen.getByTestId("cab-minutes-save"));
    await waitFor(() => expect(screen.getByTestId("cab-meeting-status")).toHaveTextContent("Durchgeführt"));
    expect(order()).toEqual(["130", "133"]);
    expect(screen.queryByTestId("cab-agenda-item-140")).toBeNull();
  });

  it("shows the end date in the header of a meeting that spans days", async () => {
    meeting.mockResolvedValue(makeMeeting({ ends_at: "2026-10-16T10:00:00Z" }));
    renderWithClient(<CabMeetingPage />);
    expect(await screen.findByTestId("cab-meeting-header")).toHaveTextContent("2026-10-15 09:00 – 2026-10-16 10:00 UTC");
  });

  it("is read only for agents who may not manage: minutes as text, no editing, no suggestions", async () => {
    status.mockResolvedValue({ can_manage: false });
    meeting.mockResolvedValue(makeMeeting({ minutes: "Zeile 1\n<b>Zeile 2</b>" }));
    renderWithClient(<CabMeetingPage />);
    const text = await screen.findByTestId("cab-minutes-text");
    expect(text.textContent).toBe("Zeile 1\n<b>Zeile 2</b>");
    expect(text.querySelector("b")).toBeNull();
    expect(screen.getByTestId("cab-agenda-note-text-130")).toHaveTextContent("Rückfallplan besprechen.");
    expect(screen.queryByTestId("cab-agenda-save")).toBeNull();
    expect(screen.queryByTestId("cab-minutes-input")).toBeNull();
    expect(screen.queryByTestId("cab-suggestions")).toBeNull();
    expect(suggestions).not.toHaveBeenCalled();
  });

  it("says when the meeting does not exist", async () => {
    meeting.mockRejectedValue(changeError(404, { code: "meeting_not_found" }));
    renderWithClient(<CabMeetingPage />);
    expect(await screen.findByTestId("cab-meeting-error")).toHaveTextContent("Die CAB-Sitzung gibt es nicht.");
  });
});
