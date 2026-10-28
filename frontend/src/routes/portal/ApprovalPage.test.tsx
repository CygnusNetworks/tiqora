import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { PortalApprovalPage } from "./ApprovalPage";

const { get, decide } = vi.hoisted(() => ({ get: vi.fn(), decide: vi.fn() }));
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, to, ...rest }: { children: React.ReactNode; to: string }) => (
    <a href={to} {...rest}>{children}</a>
  ),
  useParams: () => ({ approvalId: "601" }),
}));
vi.mock("@/lib/portalApprovalsApi", async (orig) => ({
  ...(await orig<typeof import("@/lib/portalApprovalsApi")>()),
  portalApprovalsApi: { get, decide },
}));

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <PortalApprovalPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const detail = {
  id: 601,
  title: "Zugang freigeben",
  description: "Bitte bestätigen Sie den Zugang.",
  describe_lines: ["Beantragt für: kunde1@example.org"],
  ticket_number: "2026101",
  ticket_title: "Zugang",
  status: "pending",
  step_name: "Kundenfreigabe",
  rule_text_key: "approvals.rule.quorum",
  required: 2,
  approved_count: 1,
  due_at: null,
  my_status: "pending",
  my_decision: null,
  can_decide: true,
};

beforeEach(() => {
  get.mockReset();
  decide.mockReset();
  void i18n.changeLanguage("de");
});

describe("PortalApprovalPage", () => {
  it("renders the request without any approver names", async () => {
    get.mockResolvedValue(detail);
    wrap();
    expect(await screen.findByRole("heading", { name: "Zugang freigeben" })).toBeInTheDocument();
    expect(screen.getByTestId("portal-approval-ticket")).toHaveTextContent("Ticket 2026101 · Zugang");
    expect(screen.getByTestId("portal-approval-step")).toHaveTextContent("Schritt: Kundenfreigabe");
    expect(screen.getByTestId("portal-approval-rule")).toHaveTextContent("2 Freigaben nötig · 1 von 2 Freigaben");
    expect(screen.getByTestId("portal-approval-lines")).toHaveTextContent("Beantragt für: kunde1@example.org");
    expect(screen.getByTestId("portal-approval-back").getAttribute("href")).toBe("/portal/approvals");
  });

  it("approves with an optional comment and shows the thank-you", async () => {
    get.mockResolvedValue(detail);
    decide.mockResolvedValue({ ...detail, status: "approved", my_decision: "approved", can_decide: false });
    wrap();
    fireEvent.change(await screen.findByTestId("portal-approval-comment"), { target: { value: " passt " } });
    fireEvent.click(screen.getByTestId("portal-approval-approve"));
    expect(await screen.findByTestId("portal-approval-done")).toHaveTextContent("Danke, Ihre Freigabe wurde gespeichert.");
    expect(decide).toHaveBeenCalledWith(601, { decision: "approved", comment: "passt" });
    expect(screen.queryByTestId("portal-approval-form")).toBeNull();
  });

  it("requires a comment for a rejection and sends nothing without it", async () => {
    get.mockResolvedValue(detail);
    decide.mockResolvedValue({ ...detail, status: "rejected", can_decide: false });
    wrap();
    fireEvent.click(await screen.findByTestId("portal-approval-reject"));
    expect(screen.getByTestId("portal-approval-comment-error")).toHaveTextContent("Begründung für die Ablehnung");
    expect(decide).not.toHaveBeenCalled();
    fireEvent.change(screen.getByTestId("portal-approval-comment"), { target: { value: "nein" } });
    fireEvent.click(screen.getByTestId("portal-approval-reject"));
    await waitFor(() => expect(decide).toHaveBeenCalledWith(601, { decision: "rejected", comment: "nein" }));
    expect(await screen.findByTestId("portal-approval-done")).toHaveTextContent("Ihre Ablehnung wurde gespeichert.");
  });

  it("shows the company error of the decision as text", async () => {
    get.mockResolvedValue(detail);
    decide.mockRejectedValue(new ApiError(403, { detail: { code: "customer_approver_outside_company", detail: null } }, "/x"));
    wrap();
    fireEvent.click(await screen.findByTestId("portal-approval-approve"));
    expect(await screen.findByTestId("portal-approval-decide-error")).toHaveTextContent("anderen Firma");
  });

  it("shows no form once the customer has voted", async () => {
    get.mockResolvedValue({ ...detail, can_decide: false, my_decision: "approved", my_status: "approved" });
    wrap();
    expect(await screen.findByTestId("portal-approval-mine")).toHaveTextContent("Ihre Entscheidung: Freigegeben");
    expect(screen.queryByTestId("portal-approval-form")).toBeNull();
  });

  it("shows 'Nicht verfügbar' on a 404", async () => {
    get.mockRejectedValue(new ApiError(404, { detail: "Not Found" }, "/x"));
    wrap();
    expect(await screen.findByTestId("portal-approval-error")).toHaveTextContent("Nicht verfügbar");
  });
});
