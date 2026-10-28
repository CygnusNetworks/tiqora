import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { PortalApprovalsPage } from "./ApprovalsPage";

const { list } = vi.hoisted(() => ({ list: vi.fn() }));
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, to, params, ...rest }: { children: React.ReactNode; to: string; params?: Record<string, string> }) => (
    <a href={params ? to.replace("$approvalId", params.approvalId) : to} {...rest}>{children}</a>
  ),
}));
vi.mock("@/lib/portalApprovalsApi", async (orig) => ({
  ...(await orig<typeof import("@/lib/portalApprovalsApi")>()),
  portalApprovalsApi: { list },
}));

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <PortalApprovalsPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const item = {
  id: 601,
  title: "Zugang freigeben",
  ticket_number: "2026101",
  ticket_title: "Zugang",
  step_name: "Kundenfreigabe",
  status: "pending",
  due_at: null,
  my_status: "pending",
  my_decision: null,
  can_decide: true,
  decided_at: null,
};

beforeEach(() => {
  list.mockReset();
  void i18n.changeLanguage("de");
});

describe("PortalApprovalsPage", () => {
  it("lists open approvals with title, ticket, step and a detail link", async () => {
    list.mockResolvedValue({ items: [item], total: 1, limit: 25, offset: 0 });
    wrap();
    const card = await screen.findByTestId("portal-approval-601");
    expect(card).toHaveTextContent("Zugang freigeben");
    expect(card).toHaveTextContent("2026101 · Kundenfreigabe");
    expect(card.closest("a")).toHaveAttribute("href", "/portal/approvals/601");
    expect(screen.getByTestId("approval-status-badge")).toHaveTextContent("Offen");
    expect(list).toHaveBeenCalledWith({ state: "pending", limit: 25, offset: 0 }, expect.anything());
  });

  it("loads the decided tab with state=decided and shows the decision time", async () => {
    list.mockResolvedValueOnce({ items: [item], total: 1, limit: 25, offset: 0 });
    wrap();
    await screen.findByTestId("portal-approval-601");
    list.mockResolvedValue({
      items: [{ ...item, id: 602, status: "rejected", my_status: "approved", decided_at: "2026-10-20T10:00:00Z" }],
      total: 1,
      limit: 25,
      offset: 0,
    });
    fireEvent.click(screen.getByRole("tab", { name: "Erledigt" }));
    const card = await screen.findByTestId("portal-approval-602");
    expect(card).toHaveTextContent("entschieden");
    expect(screen.getByTestId("portal-approval-mine")).toHaveTextContent("Sie haben freigegeben");
    expect(screen.getByTestId("approval-status-badge")).toHaveTextContent("Abgelehnt");
    expect(list).toHaveBeenLastCalledWith({ state: "decided", limit: 25, offset: 0 }, expect.anything());
  });

  it("pages with offset", async () => {
    list.mockResolvedValue({ items: [item], total: 30, limit: 25, offset: 0 });
    wrap();
    await screen.findByTestId("portal-approval-601");
    expect(screen.getByTestId("portal-approvals-pager")).toHaveTextContent("Seite 1 von 2");
    fireEvent.click(screen.getByTestId("portal-approvals-next"));
    await waitFor(() => expect(list).toHaveBeenLastCalledWith({ state: "pending", limit: 25, offset: 25 }, expect.anything()));
  });

  it("shows the empty state", async () => {
    list.mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0 });
    wrap();
    expect(await screen.findByTestId("portal-approvals-empty")).toHaveTextContent("Keine offenen Freigaben");
  });

  it("shows 'Nicht verfügbar' when the list answers 404", async () => {
    list.mockRejectedValue(new ApiError(404, { detail: "Not Found" }, "/x"));
    wrap();
    expect(await screen.findByTestId("portal-approvals-unavailable")).toHaveTextContent("Nicht verfügbar");
    expect(screen.queryByRole("tab")).toBeNull();
  });
});
