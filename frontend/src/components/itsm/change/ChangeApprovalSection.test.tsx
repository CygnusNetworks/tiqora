import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import i18n from "@/i18n";
import { makeApproval, makeChange, renderWithClient } from "./changeTestSupport";
import { ChangeApprovalSection } from "./ChangeApprovalSection";

const { approval, cabs, update, panel } = vi.hoisted(() => ({ approval: vi.fn(), cabs: vi.fn(), update: vi.fn(), panel: vi.fn() }));
vi.mock("@/lib/changeApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/changeApi")>("@/lib/changeApi");
  return { ...actual, changeApi: { ...actual.changeApi, approval, cabs, update } };
});
// The approvals panel has its own tests; here only what it is given counts.
vi.mock("@/components/approvals/TicketApprovalsPanel", () => ({
  TicketApprovalsPanel: (props: { ticketId: number; filter?: unknown; onChanged?: () => void }) => {
    panel(props);
    return <button type="button" data-testid="approvals-panel-stub" onClick={() => props.onChanged?.()} />;
  },
}));

const CAB = {
  id: 1, name: "Standard-CAB", description: null, rule: "majority" as const, quorum: null, is_emergency: false, valid: true,
  members: [], created_at: "2026-10-01T08:00:00Z", created_by: 1, updated_at: "2026-10-01T08:00:00Z", updated_by: 1,
  customer_members_allowed: false,
};

describe("ChangeApprovalSection", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("de");
    for (const f of [approval, cabs, update, panel]) f.mockReset();
    cabs.mockResolvedValue([CAB, { ...CAB, id: 2, name: "Notfall-CAB", is_emergency: true }]);
  });
  afterEach(cleanup);

  it("shows the snapshot of the CAB round and embeds the approval on the change page", async () => {
    approval.mockResolvedValue(makeApproval());
    renderWithClient(<ChangeApprovalSection change={makeChange({ phase: "approval", approval_round: 1 })} embedApprovals />);
    const snap = await screen.findByTestId("change-approval-snapshot");
    expect(snap).toHaveTextContent("Offen");
    expect(snap).toHaveTextContent("Runde 1");
    expect(snap).toHaveTextContent("1 von 3 Zustimmungen");
    expect(screen.getByTestId("approvals-panel-stub")).toBeInTheDocument();
    expect(panel).toHaveBeenCalledWith(
      expect.objectContaining({ ticketId: 130, filter: { subjectType: "itsm.change", approvalKey: "change_cab" } }),
    );
  });

  it("reloads the change after a vote in the embedded panel, and again when the outcome job had time", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      approval.mockResolvedValue(makeApproval());
      renderWithClient(<ChangeApprovalSection change={makeChange({ phase: "approval" })} embedApprovals />);
      fireEvent.click(await screen.findByTestId("approvals-panel-stub"));
      await waitFor(() => expect(approval).toHaveBeenCalledTimes(2));
      await act(async () => {
        vi.advanceTimersByTime(3000);
      });
      await waitFor(() => expect(approval).toHaveBeenCalledTimes(3));
    } finally {
      vi.useRealTimers();
    }
  });

  it("names the emergency CAB as the default of an emergency change", async () => {
    approval.mockResolvedValue({ approval_id: null, approval_round: 0, approval_via: null, approved_at: null, history_missing: false, snapshot: null });
    renderWithClient(<ChangeApprovalSection change={makeChange({ phase: "draft", change_type: "emergency", cab_id: null })} embedApprovals />);
    await waitFor(() => expect(screen.getByTestId("change-approval-cab")).toBeEnabled());
    expect(screen.getByTestId("change-approval-cab")).toHaveTextContent("Notfall-CAB laut Einstellungen");
  });

  it("shows the snapshot only in the zoom and points to the approvals section", async () => {
    approval.mockResolvedValue(makeApproval());
    renderWithClient(<ChangeApprovalSection change={makeChange({ phase: "approval" })} embedApprovals={false} />);
    expect(await screen.findByTestId("change-approval-snapshot")).toBeInTheDocument();
    expect(screen.getByTestId("change-approval-vote-hint")).toHaveTextContent("Abstimmen im Abschnitt Freigaben");
    expect(screen.queryByTestId("approvals-panel-stub")).toBeNull();
  });

  it("says that the history is gone when the engine no longer has the approval", async () => {
    approval.mockResolvedValue(makeApproval({ snapshot: null, history_missing: false }));
    renderWithClient(<ChangeApprovalSection change={makeChange({ phase: "scheduled" })} embedApprovals />);
    expect(await screen.findByTestId("change-approval-history-missing")).toHaveTextContent("Freigabeverlauf nicht mehr vorhanden");
    expect(screen.queryByTestId("approvals-panel-stub")).toBeNull();
    cleanup();
    approval.mockResolvedValue(makeApproval({ snapshot: null, history_missing: true }));
    renderWithClient(<ChangeApprovalSection change={makeChange({ phase: "scheduled" })} embedApprovals={false} />);
    expect(await screen.findByTestId("change-approval-history-missing")).toBeInTheDocument();
  });

  it("explains a rejection because the CAB had nobody to vote", async () => {
    const base = makeApproval();
    approval.mockResolvedValue({ ...base, snapshot: { ...base.snapshot!, status: "rejected", reason: "no_approvers", decided_at: "2026-10-12T09:00:00Z" } });
    renderWithClient(<ChangeApprovalSection change={makeChange({ phase: "rejected" })} embedApprovals={false} />);
    expect(await screen.findByTestId("change-approval-no-approvers")).toHaveTextContent("Keine gültigen CAB-Mitglieder");
    expect(screen.getByTestId("change-approval-snapshot")).toHaveTextContent("2026-10-12 09:00");
  });

  it("names an approval that came from the template", async () => {
    approval.mockResolvedValue({ approval_id: null, approval_round: 0, approval_via: "template", approved_at: "2026-10-12T08:00:00Z", history_missing: false, snapshot: null });
    renderWithClient(<ChangeApprovalSection change={makeChange({ change_type: "standard", phase: "scheduled" })} embedApprovals />);
    expect(await screen.findByTestId("change-approval-via")).toHaveTextContent("Vorab genehmigt durch die Standard-Vorlage");
    expect(screen.queryByTestId("change-approval-history-missing")).toBeNull();
  });

  it("lets the CAB be chosen before the approval is requested", async () => {
    approval.mockResolvedValue({ approval_id: null, approval_round: 0, approval_via: null, approved_at: null, history_missing: false, snapshot: null });
    update.mockResolvedValue(makeChange({ cab_id: 2 }));
    renderWithClient(<ChangeApprovalSection change={makeChange({ phase: "assessment" })} embedApprovals />);
    expect(await screen.findByTestId("change-approval-none")).toBeInTheDocument();
    fireEvent.click(await screen.findByTestId("change-approval-cab"));
    fireEvent.click(await screen.findByTestId("change-approval-cab-menu-option-2"));
    await waitFor(() => expect(update).toHaveBeenCalledWith(130, { cab_id: 2 }));
  });

  it("offers no CAB choice while the CAB decides", async () => {
    approval.mockResolvedValue(makeApproval());
    renderWithClient(<ChangeApprovalSection change={makeChange({ phase: "approval" })} embedApprovals={false} />);
    await screen.findByTestId("change-approval-snapshot");
    expect(screen.queryByTestId("change-approval-cab")).toBeNull();
  });
});
