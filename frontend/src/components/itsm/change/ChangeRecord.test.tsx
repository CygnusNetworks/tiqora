import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import i18n from "@/i18n";
import { makeChange, renderWithClient } from "./changeTestSupport";
import { ChangeRecord } from "./ChangeRecord";

const { transition, sections, stub } = vi.hoisted(() => {
  const sections = vi.fn();
  // The sections have their own tests; the record only lays them out.
  const stub = (name: string) => (props: Record<string, unknown>) => {
    sections(name, props);
    return <div data-testid={`stub-${name}`} />;
  };
  return { transition: vi.fn(), sections, stub };
});
vi.mock("@/lib/changeApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/changeApi")>("@/lib/changeApi");
  return { ...actual, changeApi: { ...actual.changeApi, transition } };
});
vi.mock("./ChangePlansSection", () => ({ ChangePlansSection: stub("plans") }));
vi.mock("./ChangeTasksSection", () => ({ ChangeTasksSection: stub("tasks") }));
vi.mock("./ChangeRiskSection", () => ({ ChangeRiskSection: stub("risk") }));
vi.mock("./ChangeImpactSection", () => ({ ChangeImpactSection: stub("impact") }));
vi.mock("./ConflictList", () => ({ ConflictList: stub("conflicts") }));
vi.mock("./ChangeApprovalSection", () => ({ ChangeApprovalSection: stub("approval") }));

describe("ChangeRecord", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("de");
    transition.mockReset();
    sections.mockReset();
  });
  afterEach(cleanup);

  it("draws the phase bar and one button per allowed target", () => {
    renderWithClient(<ChangeRecord change={makeChange({ phase: "assessment", allowed_targets: ["approval", "cancelled"] })} embedApprovals />);
    const bar = screen.getByTestId("change-phase-bar");
    expect(within(bar).getByTestId("change-phase-assessment")).toHaveAttribute("aria-current", "step");
    const actions = screen.getByTestId("change-actions");
    expect(within(actions).getAllByRole("button").map((b) => b.getAttribute("data-target"))).toEqual(["approval", "cancelled"]);
    expect(within(actions).getByTestId("change-action-approval")).toHaveTextContent("Freigabe anfordern");
    expect(within(actions).getByTestId("change-action-cancelled")).toHaveTextContent("Change abbrechen");
  });

  it("names the step by where it starts", () => {
    renderWithClient(<ChangeRecord change={makeChange({ phase: "scheduled", allowed_targets: ["implementing", "draft", "cancelled"] })} embedApprovals />);
    expect(screen.getByTestId("change-action-draft")).toHaveTextContent("Neu planen");
    cleanup();
    renderWithClient(<ChangeRecord change={makeChange({ phase: "rejected", allowed_targets: ["draft"] })} embedApprovals />);
    expect(screen.getByTestId("change-action-draft")).toHaveTextContent("Überarbeiten");
  });

  it("shows no buttons without write access", () => {
    renderWithClient(<ChangeRecord change={makeChange({ can_write: false })} embedApprovals />);
    expect(screen.queryByTestId("change-actions")).toBeNull();
  });

  it("opens the transition dialog and moves the change", async () => {
    transition.mockResolvedValue(makeChange({ phase: "approval", allowed_targets: ["cancelled"] }));
    renderWithClient(<ChangeRecord change={makeChange()} embedApprovals />);
    fireEvent.click(screen.getByTestId("change-action-approval"));
    fireEvent.click(await screen.findByTestId("change-transition-submit"));
    await waitFor(() => expect(transition).toHaveBeenCalledWith(130, { to: "approval" }));
    await waitFor(() => expect(screen.queryByTestId("change-transition-submit")).toBeNull());
  });

  it("asks for the outcome and the review in the PIR dialog when closing", async () => {
    renderWithClient(<ChangeRecord change={makeChange({ phase: "review", allowed_targets: ["closed"] })} embedApprovals />);
    fireEvent.click(screen.getByTestId("change-action-closed"));
    expect(await screen.findByTestId("change-pir-outcome")).toBeInTheDocument();
    expect(screen.getByTestId("change-pir-notes")).toBeInTheDocument();
  });

  it("warns when the dynamic fields drifted from the record", () => {
    renderWithClient(<ChangeRecord change={makeChange({ drift: ["phase_field"] })} embedApprovals />);
    expect(screen.getByTestId("change-drift-banner")).toHaveTextContent("Phase");
    expect(screen.queryByTestId("change-ticket-closed-banner")).toBeNull();
  });

  it("warns when the ticket was closed although the change is still open", () => {
    renderWithClient(<ChangeRecord change={makeChange({ drift: ["ticket_closed"], ticket_state_type: "closed" })} embedApprovals />);
    expect(screen.getByTestId("change-ticket-closed-banner")).toHaveTextContent("Das Ticket wurde geschlossen");
    expect(screen.queryByTestId("change-drift-banner")).toBeNull();
  });

  it("lays out all sections and hands the approvals choice on", () => {
    renderWithClient(<ChangeRecord change={makeChange()} embedApprovals={false} />);
    for (const name of ["plans", "tasks", "risk", "impact", "conflicts", "approval"]) {
      expect(screen.getByTestId(`stub-${name}`)).toBeInTheDocument();
    }
    expect(sections).toHaveBeenCalledWith("approval", expect.objectContaining({ embedApprovals: false }));
    expect(sections).toHaveBeenCalledWith("conflicts", { ticketId: 130 });
  });
});
