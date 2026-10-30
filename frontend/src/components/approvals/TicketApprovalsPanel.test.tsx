import { beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { ApiError } from "@/lib/api";
import { resolveApprovalsData } from "@/demo/approvalsMock";
import { TicketApprovalsPanel } from "./TicketApprovalsPanel";
import { demoDetail, mockApi, renderApproval, useGerman } from "@/test/approvalsHarness";

beforeAll(useGerman);

const TICKET = 130;
const LIST = `GET /api/v1/tickets/${TICKET}/approvals`;
const TICKET_PATH = `GET /api/v1/tickets/${TICKET}`;
const summaries = () => resolveApprovalsData(`/api/v1/tickets/${TICKET}/approvals`, "GET") as unknown[];
const rw = () => ({ id: TICKET, title: "Switch-Firmware", permissions: { ro: true, rw: true, note: true } });
const noteOnly = () => ({ id: TICKET, title: "Switch-Firmware", permissions: { ro: true, rw: false, note: true } });

describe("TicketApprovalsPanel", () => {
  it("is hidden without approvals when the agent cannot request", async () => {
    const spy = mockApi({ [LIST]: () => [], [TICKET_PATH]: noteOnly });
    renderApproval(<TicketApprovalsPanel ticketId={TICKET} />);
    await waitFor(() => expect(spy.mock.calls.some((c) => String(c[1]).endsWith("/approvals"))).toBe(true));
    await waitFor(() => expect(spy.mock.calls.some((c) => c[1] === `/api/v1/tickets/${TICKET}`)).toBe(true));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId("ticket-approvals-panel")).toBeNull();
  });

  it("shows only the request button when empty and the agent has rw (not just note)", async () => {
    mockApi({ [LIST]: () => [], [TICKET_PATH]: rw });
    renderApproval(<TicketApprovalsPanel ticketId={TICKET} />);
    expect(await screen.findByTestId("ticket-approvals-request")).toHaveTextContent("Freigabe anfordern");
    expect(screen.getByRole("heading", { name: "Freigaben" })).toBeInTheDocument();
    expect(screen.queryByTestId("ticket-approval")).toBeNull();
  });

  it("lists the pending approval with status, title and stage, without a request button for note-only agents", async () => {
    mockApi({
      [LIST]: summaries,
      [TICKET_PATH]: noteOnly,
      "GET /api/v1/approvals/501": () => demoDetail(501),
    });
    renderApproval(<TicketApprovalsPanel ticketId={TICKET} />);
    const item = await screen.findByTestId("ticket-approval");
    expect(item).toHaveAttribute("data-approval-id", "501");
    expect(within(item).getByTestId("ticket-approval-title")).toHaveTextContent("Change freigeben: Switch-Firmware");
    expect(within(item).getByTestId("approval-status-badge")).toHaveTextContent("Offen");
    expect(within(item).getByTestId("ticket-approval-meta")).toHaveTextContent("Stufe 1");
    expect(screen.queryByTestId("ticket-approvals-request")).toBeNull();
  });

  it("decides from the panel and refreshes the lists", async () => {
    const decide = vi.fn(() => demoDetail(501));
    const spy = mockApi({
      [LIST]: summaries,
      [TICKET_PATH]: rw,
      "GET /api/v1/approvals/501": () => demoDetail(501),
      "POST /api/v1/approvals/501/decision": decide,
    });
    renderApproval(<TicketApprovalsPanel ticketId={TICKET} />);
    fireEvent.click(await screen.findByTestId("ticket-approval-reject"));
    expect(screen.getByTestId("decision-choice-rejected")).toHaveAttribute("aria-checked", "true");
    fireEvent.change(screen.getByTestId("decision-comment"), { target: { value: "Zu riskant" } });
    const before = spy.mock.calls.filter((c) => c[1] === `/api/v1/tickets/${TICKET}/approvals`).length;
    fireEvent.click(screen.getByTestId("decision-submit"));
    await waitFor(() => expect(decide).toHaveBeenCalledTimes(1));
    expect((decide.mock.calls[0] as unknown as [{ body: unknown }])[0].body).toEqual({
      decision: "rejected",
      comment: "Zu riskant",
      approver_id: 9002,
    });
    await waitFor(() =>
      expect(spy.mock.calls.filter((c) => c[1] === `/api/v1/tickets/${TICKET}/approvals`).length).toBeGreaterThan(before),
    );
    expect(screen.queryByTestId("decision-submit")).toBeNull();
  });

  it("asks before withdrawing and only then calls cancel", async () => {
    const cancel = vi.fn(() => ({ ...demoDetail(501), status: "cancelled" }));
    mockApi({
      [LIST]: summaries,
      [TICKET_PATH]: rw,
      "GET /api/v1/approvals/501": () => demoDetail(501),
      "POST /api/v1/approvals/501/cancel": cancel,
    });
    renderApproval(<TicketApprovalsPanel ticketId={TICKET} />);
    fireEvent.click(await screen.findByTestId("ticket-approval-cancel"));
    expect(cancel).not.toHaveBeenCalled();
    expect(screen.getByText("Anforderung zurückziehen?")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));
    await waitFor(() => expect(cancel).toHaveBeenCalledTimes(1));
    expect((cancel.mock.calls[0] as unknown as [{ body?: unknown } | undefined])[0]?.body).toBeUndefined();
  });

  it("tells the embedding feature after a write (the change page reloads the change)", async () => {
    const onChanged = vi.fn();
    mockApi({
      [LIST]: summaries,
      [TICKET_PATH]: rw,
      "GET /api/v1/approvals/501": () => demoDetail(501),
      "POST /api/v1/approvals/501/cancel": () => ({ ...demoDetail(501), status: "cancelled" }),
    });
    renderApproval(<TicketApprovalsPanel ticketId={TICKET} onChanged={onChanged} />);
    fireEvent.click(await screen.findByTestId("ticket-approval-cancel"));
    expect(onChanged).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
  });

  it("shows the plain-language error when withdrawing fails", async () => {
    mockApi({
      [LIST]: summaries,
      [TICKET_PATH]: rw,
      "GET /api/v1/approvals/501": () => demoDetail(501),
      "POST /api/v1/approvals/501/cancel": () => {
        throw new ApiError(409, { detail: { code: "approval_closed", detail: null } }, "/x");
      },
    });
    renderApproval(<TicketApprovalsPanel ticketId={TICKET} />);
    fireEvent.click(await screen.findByTestId("ticket-approval-cancel"));
    fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));
    expect(await screen.findByTestId("withdraw-error")).toHaveTextContent("Diese Freigabe ist bereits abgeschlossen.");
  });

  it("shows the latest refresh error only, and none from an earlier withdraw attempt", async () => {
    mockApi({
      [LIST]: summaries,
      [TICKET_PATH]: rw,
      "GET /api/v1/approvals/501": () => demoDetail(501),
      "POST /api/v1/approvals/501/cancel": () => {
        throw new ApiError(409, { detail: { code: "approval_closed", detail: null } }, "/x");
      },
      "POST /api/v1/approvals/501/refresh-approvers": () => {
        throw new ApiError(403, { detail: { code: "forbidden", detail: null } }, "/x");
      },
    });
    renderApproval(<TicketApprovalsPanel ticketId={TICKET} />);
    fireEvent.click(await screen.findByTestId("ticket-approval-cancel"));
    fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));
    await screen.findByTestId("withdraw-error");
    fireEvent.click(screen.getByTestId("confirm-dialog-cancel"));
    expect(screen.queryByTestId("ticket-approval-error")).toBeNull();
    fireEvent.click(await screen.findByTestId("ticket-approval-refresh"));
    expect(await screen.findByTestId("ticket-approval-error")).toHaveTextContent("Dazu haben Sie keine Berechtigung.");
    expect(screen.queryByTestId("withdraw-error")).toBeNull();
  });

  it("votes with the caller's own row and says when a row is a substitution", async () => {
    const decide = vi.fn(() => demoDetail(501));
    const detail = () => {
      const d = demoDetail(501);
      d.my_rows = [
        { approver_id: 9100, step_id: 7001, on_behalf_of_label: "Erika Muster", can_forward: false },
        { approver_id: 9002, step_id: 7001, on_behalf_of_label: null, can_forward: true },
      ];
      return d;
    };
    mockApi({
      [LIST]: summaries,
      [TICKET_PATH]: rw,
      "GET /api/v1/approvals/501": detail,
      "POST /api/v1/approvals/501/decision": decide,
    });
    renderApproval(<TicketApprovalsPanel ticketId={TICKET} />);
    fireEvent.click(await screen.findByTestId("ticket-approval-approve"));
    fireEvent.click(screen.getByTestId("decision-submit"));
    await waitFor(() => expect(decide).toHaveBeenCalledTimes(1));
    expect((decide.mock.calls[0] as unknown as [{ body: { approver_id: number } }])[0].body.approver_id).toBe(9002);
    expect(screen.queryByTestId("ticket-approval-on-behalf")).toBeNull();
  });

  it("names the person when only a substitute row is left", async () => {
    const detail = () => {
      const d = demoDetail(501);
      d.my_rows = [{ approver_id: 9100, step_id: 7001, on_behalf_of_label: "Erika Muster", can_forward: false }];
      return d;
    };
    mockApi({ [LIST]: summaries, [TICKET_PATH]: rw, "GET /api/v1/approvals/501": detail });
    renderApproval(<TicketApprovalsPanel ticketId={TICKET} />);
    expect(await screen.findByTestId("ticket-approval-on-behalf")).toHaveTextContent("in Vertretung für Erika Muster");
  });

  it("offers no request button on a filtered panel, even with rw", async () => {
    const base = summaries()[0] as Record<string, unknown>;
    mockApi({
      [LIST]: () => [{ ...base, id: 7, subject_type: "itsm.change", approval_key: "change_cab", title: "CAB", my_pending: false }],
      [TICKET_PATH]: rw,
    });
    renderApproval(<TicketApprovalsPanel ticketId={TICKET} filter={{ subjectType: "itsm.change", approvalKey: "change_cab" }} />);
    await screen.findByTestId("ticket-approvals-list");
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId("ticket-approvals-request")).toBeNull();
  });

  it("re-determines approvers and expands steps and history under Details", async () => {
    const refresh = vi.fn(() => demoDetail(501));
    mockApi({
      [LIST]: summaries,
      [TICKET_PATH]: rw,
      "GET /api/v1/approvals/501": () => demoDetail(501),
      "POST /api/v1/approvals/501/refresh-approvers": refresh,
    });
    renderApproval(<TicketApprovalsPanel ticketId={TICKET} />);
    fireEvent.click(await screen.findByTestId("ticket-approval-details"));
    expect(await screen.findByTestId("ticket-approval-body")).toBeInTheDocument();
    expect(await screen.findByText("Betriebsleitung")).toBeInTheDocument();
    fireEvent.click(await screen.findByTestId("ticket-approval-refresh"));
    await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
  });

  it("collapses withdrawn approvals under Ältere Freigaben, newest first", async () => {
    const base = summaries()[0] as Record<string, unknown>;
    const mk = (id: number, status: string, created_at: string) => ({
      ...base,
      id,
      status,
      created_at,
      my_pending: false,
      can_cancel: false,
      title: `Freigabe ${id}`,
    });
    mockApi({
      [LIST]: () => [
        mk(1, "approved", "2026-10-01T10:00:00"),
        mk(2, "cancelled", "2026-10-02T10:00:00"),
        mk(3, "rejected", "2026-10-03T10:00:00"),
      ],
      [TICKET_PATH]: noteOnly,
    });
    renderApproval(<TicketApprovalsPanel ticketId={TICKET} />);
    const list = await screen.findByTestId("ticket-approvals-list");
    expect(within(list).getAllByTestId("ticket-approval-title").map((n) => n.textContent)).toEqual([
      "Freigabe 3",
      "Freigabe 1",
    ]);
    expect(screen.queryByTestId("ticket-approvals-older")).toBeNull();
    fireEvent.click(screen.getByTestId("ticket-approvals-older-toggle"));
    expect(screen.getByTestId("ticket-approvals-older-toggle")).toHaveTextContent("Ältere Freigaben (1)");
    expect(within(screen.getByTestId("ticket-approvals-older")).getByText("Freigabe 2")).toBeInTheDocument();
  });

  it("filter prop keeps only the matching subject type and key; title overrides the heading", async () => {
    const base = summaries()[0] as Record<string, unknown>;
    const mk = (id: number, subject_type: string, approval_key: string) => ({
      ...base,
      id,
      subject_type,
      approval_key,
      my_pending: false,
      title: `A${id}`,
    });
    mockApi({
      [LIST]: () => [
        mk(1, "ticket", "ticket_free"),
        mk(2, "itsm.change", "change_cab"),
        mk(3, "itsm.change", "change_other"),
      ],
      [TICKET_PATH]: noteOnly,
    });
    renderApproval(
      <TicketApprovalsPanel
        ticketId={TICKET}
        filter={{ subjectType: "itsm.change", approvalKey: "change_cab" }}
        title="CAB-Freigabe"
      />,
    );
    await screen.findByTestId("ticket-approvals-list");
    expect(screen.getAllByTestId("ticket-approval-title").map((n) => n.textContent)).toEqual(["A2"]);
    expect(screen.getByRole("heading", { name: "CAB-Freigabe" })).toBeInTheDocument();
  });
});
