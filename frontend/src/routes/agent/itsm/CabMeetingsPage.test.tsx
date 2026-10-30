import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import i18n from "@/i18n";
import { changeError, renderWithClient } from "@/components/itsm/change/changeTestSupport";
import type { CabMeetingListItem } from "@/lib/changeApi";
import { CabMeetingsPage } from "./CabMeetingsPage";

const { meetings, status, cabs, createMeeting, navigate } = vi.hoisted(() => ({
  meetings: vi.fn(),
  status: vi.fn(),
  cabs: vi.fn(),
  createMeeting: vi.fn(),
  navigate: vi.fn(),
}));
vi.mock("@tanstack/react-router", async () => {
  const actual = await vi.importActual<typeof import("@tanstack/react-router")>("@tanstack/react-router");
  return { ...actual, useNavigate: () => navigate };
});
vi.mock("@/lib/changeApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/changeApi")>("@/lib/changeApi");
  return { ...actual, changeApi: { ...actual.changeApi, meetings, status, cabs, createMeeting } };
});

const meeting = (id: number, over: Partial<CabMeetingListItem> = {}): CabMeetingListItem => ({
  id,
  cab_id: 1,
  cab_name: "CAB Netzwerk",
  title: `Sitzung ${id}`,
  starts_at: "2026-10-15T09:00:00Z",
  ends_at: "2026-10-15T10:00:00Z",
  location: "Raum 2",
  status: "planned",
  agenda_count: 2,
  created_at: "2026-10-01T08:00:00Z",
  created_by: 1,
  updated_at: "2026-10-01T08:00:00Z",
  updated_by: 1,
  ...over,
});

describe("CabMeetingsPage", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("de");
    vi.useFakeTimers({ toFake: ["Date"], now: new Date("2026-10-20T12:00:00Z") });
    for (const m of [meetings, status, cabs, createMeeting, navigate]) m.mockReset();
    status.mockResolvedValue({ can_manage: false, can_see_legacy: true, legacy: "live" });
    cabs.mockResolvedValue([{ id: 1, name: "CAB Netzwerk" }]);
  });
  afterEach(() => {
    vi.useRealTimers();
    cleanup();
  });

  it("lists the meetings of the current UTC month, earliest first, with status and agenda size", async () => {
    meetings.mockResolvedValue([meeting(2, { starts_at: "2026-10-22T09:00:00Z", status: "held" }), meeting(1)]);
    renderWithClient(<CabMeetingsPage />);
    const rows = await screen.findAllByTestId(/^cab-meeting-\d+$/);
    expect(rows.map((r) => r.getAttribute("data-testid"))).toEqual(["cab-meeting-1", "cab-meeting-2"]);
    expect(rows[0]).toHaveTextContent("CAB Netzwerk");
    expect(rows[0]).toHaveTextContent("2026-10-15 09:00 – 10:00 UTC");
    expect(rows[0]).toHaveTextContent("2 Punkte");
    expect(rows[1]).toHaveAttribute("data-status", "held");
    expect(rows[1]).toHaveTextContent("Durchgeführt");
    expect(screen.getByRole("link", { name: "Sitzung 1" })).toHaveAttribute("href", "/agent/itsm/cab-meetings/1");
    expect(meetings).toHaveBeenCalledWith("2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z", expect.anything());
    expect(screen.queryByTestId("cab-meetings-create")).toBeNull();
  });

  it("steps month by month, across the year end", async () => {
    meetings.mockResolvedValue([]);
    renderWithClient(<CabMeetingsPage />);
    expect(await screen.findByTestId("cab-meetings-empty")).toHaveTextContent("keine CAB-Sitzung");
    fireEvent.click(screen.getByTestId("cab-meetings-next"));
    fireEvent.click(screen.getByTestId("cab-meetings-next"));
    fireEvent.click(screen.getByTestId("cab-meetings-next"));
    await waitFor(() => expect(meetings).toHaveBeenLastCalledWith("2027-01-01T00:00:00Z", "2027-02-01T00:00:00Z", expect.anything()));
    expect(screen.getByTestId("cab-meetings-label")).toHaveTextContent("Januar 2027");
    fireEvent.click(screen.getByTestId("cab-meetings-today"));
    await waitFor(() => expect(screen.getByTestId("cab-meetings-label")).toHaveTextContent("Oktober 2026"));
  });

  it("offers planning only to change managers and opens the new meeting", async () => {
    status.mockResolvedValue({ can_manage: true });
    meetings.mockResolvedValue([]);
    createMeeting.mockResolvedValue(meeting(7));
    renderWithClient(<CabMeetingsPage />);
    fireEvent.click(await screen.findByTestId("cab-meetings-create"));
    expect(screen.getByTestId("cab-create-submit")).toBeDisabled();
    fireEvent.click(await screen.findByTestId("cab-create-cab"));
    fireEvent.click(await screen.findByTestId("cab-create-cab-menu-option-1"));
    fireEvent.change(screen.getByTestId("cab-create-title"), { target: { value: " CAB November " } });
    fireEvent.change(screen.getByTestId("cab-create-start"), { target: { value: "2026-11-12T09:00" } });
    fireEvent.change(screen.getByTestId("cab-create-end"), { target: { value: "2026-11-12T08:00" } });
    expect(screen.getByTestId("cab-create-order")).toBeInTheDocument();
    expect(screen.getByTestId("cab-create-submit")).toBeDisabled();
    fireEvent.change(screen.getByTestId("cab-create-end"), { target: { value: "2026-11-12T10:00" } });
    fireEvent.change(screen.getByTestId("cab-create-location"), { target: { value: "Raum 2" } });
    fireEvent.click(screen.getByTestId("cab-create-submit"));
    await waitFor(() =>
      expect(createMeeting).toHaveBeenCalledWith({
        cab_id: 1,
        title: "CAB November",
        starts_at: "2026-11-12T09:00:00Z",
        ends_at: "2026-11-12T10:00:00Z",
        location: "Raum 2",
      }),
    );
    await waitFor(() => expect(navigate).toHaveBeenCalledWith({ to: "/agent/itsm/cab-meetings/$meetingId", params: { meetingId: "7" } }));
  });

  it("shows a failed create by its code", async () => {
    status.mockResolvedValue({ can_manage: true });
    meetings.mockResolvedValue([]);
    createMeeting.mockRejectedValue(changeError(422, { code: "cab_invalid" }));
    renderWithClient(<CabMeetingsPage />);
    fireEvent.click(await screen.findByTestId("cab-meetings-create"));
    fireEvent.click(await screen.findByTestId("cab-create-cab"));
    fireEvent.click(await screen.findByTestId("cab-create-cab-menu-option-1"));
    fireEvent.change(screen.getByTestId("cab-create-title"), { target: { value: "X" } });
    fireEvent.change(screen.getByTestId("cab-create-start"), { target: { value: "2026-11-12T09:00" } });
    fireEvent.change(screen.getByTestId("cab-create-end"), { target: { value: "2026-11-12T10:00" } });
    fireEvent.click(screen.getByTestId("cab-create-submit"));
    expect(await screen.findByTestId("cab-create-error")).toHaveTextContent("Das CAB ist ungültig.");
  });

  it("points to the Znuny changes in Mode A", async () => {
    meetings.mockRejectedValue(changeError(409, { code: "change_mode_a" }));
    renderWithClient(<CabMeetingsPage />);
    expect(await screen.findByTestId("cab-meetings-error")).toHaveTextContent("Modus A");
    expect(await screen.findByRole("link", { name: "Changes (Znuny)" })).toHaveAttribute("href", "/agent/itsm/legacy-changes");
  });

  it("offers no Znuny link to an agent who may not see the Znuny changes", async () => {
    status.mockResolvedValue({ can_manage: false, can_see_legacy: false, legacy: "live" });
    meetings.mockRejectedValue(changeError(409, { code: "change_mode_a" }));
    renderWithClient(<CabMeetingsPage />);
    const error = await screen.findByTestId("cab-meetings-error");
    await waitFor(() => expect(status).toHaveBeenCalled());
    expect(error.querySelector("a")).toBeNull();
  });

  it("shows the end date of a meeting that spans days", async () => {
    meetings.mockResolvedValue([meeting(1, { starts_at: "2026-10-15T09:00:00Z", ends_at: "2026-10-16T10:00:00Z" }), meeting(2, { starts_at: "2026-10-17T09:00:00Z", ends_at: "2026-10-17T10:00:00Z" })]);
    renderWithClient(<CabMeetingsPage />);
    expect(await screen.findByTestId("cab-meeting-1")).toHaveTextContent("2026-10-15 09:00 – 2026-10-16 10:00 UTC");
    expect(screen.getByTestId("cab-meeting-2")).toHaveTextContent("2026-10-17 09:00 – 10:00 UTC");
  });

  it("says so when there is no CAB to hold a meeting, and when the CABs do not load", async () => {
    status.mockResolvedValue({ can_manage: true, can_see_legacy: false, legacy: "none" });
    meetings.mockResolvedValue([]);
    cabs.mockResolvedValueOnce([]);
    renderWithClient(<CabMeetingsPage />);
    fireEvent.click(await screen.findByTestId("cab-meetings-create"));
    expect(await screen.findByTestId("cab-create-no-cabs")).toHaveTextContent("Es gibt noch kein CAB.");
    cleanup();
    cabs.mockRejectedValue(changeError(500, { code: "boom" }));
    renderWithClient(<CabMeetingsPage />);
    fireEvent.click(await screen.findByTestId("cab-meetings-create"));
    expect(await screen.findByTestId("cab-create-cabs-error")).toBeInTheDocument();
    expect(screen.queryByTestId("cab-create-no-cabs")).toBeNull();
  });
});
