import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { ApprovalsPage } from "./ApprovalsPage";
import { demoDetail, mockApi, renderApproval, useGerman } from "@/test/approvalsHarness";
import { demoApprovalInbox } from "@/demo/approvalsMock";

const navigate = vi.fn();
let searchParams: { id?: number; tab?: string } = {};

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
  useSearch: () => searchParams,
  Link: ({ children, to, params, ...rest }: { children: React.ReactNode; to: string; params?: Record<string, string> }) => (
    <a href={`${to.replace("$ticketId", params?.ticketId ?? "")}`} {...rest}>
      {children}
    </a>
  ),
}));

beforeAll(useGerman);

function setMatchMedia(matches: boolean) {
  window.matchMedia = ((q: string) => ({
    matches,
    media: q,
    addEventListener: () => {},
    removeEventListener: () => {},
  })) as never;
}

const inbox = () => demoApprovalInbox("pending");

function api(extra: NonNullable<Parameters<typeof mockApi>[0]> = {}) {
  return mockApi({
    "GET /api/v1/approvals/inbox": () => inbox(),
    "GET /api/v1/approvals/inbox/count": () => ({ pending: 2 }),
    "GET /api/v1/approvals/501": () => demoDetail(501),
    "GET /api/v1/approvals/503": () => demoDetail(503),
    "GET /api/v1/approvals/delegations": () => [],
    ...extra,
  });
}

beforeEach(() => {
  navigate.mockReset();
  searchParams = {};
  setMatchMedia(false);
});

describe("ApprovalsPage", () => {
  it("lists the demo inbox with the overdue request first", async () => {
    api();
    renderApproval(<ApprovalsPage />);
    const rows = await screen.findAllByTestId("approval-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveAttribute("data-approval-id", "503");
    expect(within(rows[0]).getByTestId("approval-row-overdue")).toHaveTextContent("überfällig");
    expect(rows[1]).toHaveAttribute("data-approval-id", "501");
    await waitFor(() => expect(screen.getByRole("tab", { name: /Offen/ })).toHaveTextContent("2"));
  });

  it("shows the empty state", async () => {
    api({ "GET /api/v1/approvals/inbox": () => ({ items: [], total: 0, limit: 25, offset: 0 }) });
    renderApproval(<ApprovalsPage />);
    expect(await screen.findByTestId("approvals-empty")).toHaveTextContent(
      "Keine offenen Freigaben. Sie werden per E-Mail benachrichtigt, wenn eine Freigabe auf Sie wartet.",
    );
  });

  it("selecting a row navigates to ?id=", async () => {
    api();
    renderApproval(<ApprovalsPage />);
    fireEvent.click((await screen.findAllByTestId("approval-row"))[1]);
    expect(navigate).toHaveBeenCalledWith({ to: "/agent/approvals", search: { tab: undefined, id: 501 } });
  });

  it("deep link opens the panel with steps, description, lines, ticket link and buttons", async () => {
    searchParams = { id: 501 };
    api();
    renderApproval(<ApprovalsPage />);
    expect(await screen.findByTestId("approval-panel")).toBeInTheDocument();
    expect(await screen.findByTestId("approval-detail-title")).toHaveTextContent("Change freigeben");
    expect(screen.getAllByTestId("approval-step")).toHaveLength(2);
    expect(screen.getByTestId("approval-description")).toHaveTextContent("Wartungsfenster Samstag");
    expect(screen.getByTestId("approval-describe-lines")).toHaveTextContent("Risiko: mittel");
    expect(screen.getByTestId("approval-ticket-link")).toHaveAttribute("href", "/agent/tickets/130");
    expect(screen.getByTestId("approval-approve")).toHaveTextContent("Freigeben");
    expect(screen.getByTestId("approval-reject")).toHaveTextContent("Ablehnen");
    expect(screen.getByTestId("approval-forward")).toHaveTextContent("Weiterleiten");
    expect(screen.getByTestId("approval-audit")).toBeInTheDocument();
  });

  it("hides the ticket link and forward button when the API says so", async () => {
    searchParams = { id: 501 };
    const d = demoDetail(501);
    d.ticket_link = false;
    d.my_rows[0].can_forward = false;
    api({ "GET /api/v1/approvals/501": () => d });
    renderApproval(<ApprovalsPage />);
    await screen.findByTestId("approval-approve");
    expect(screen.queryByTestId("approval-ticket-link")).toBeNull();
    expect(screen.queryByTestId("approval-forward")).toBeNull();
  });

  it("approves: posts the decision for the listed approver row and refreshes inbox and count", async () => {
    searchParams = { id: 501 };
    const decide = vi.fn(() => demoDetail(501));
    const count = vi.fn(() => ({ pending: 2 }));
    api({ "POST /api/v1/approvals/501/decision": decide, "GET /api/v1/approvals/inbox/count": count });
    renderApproval(<ApprovalsPage />);
    fireEvent.click(await screen.findByTestId("approval-approve"));
    fireEvent.click(await screen.findByTestId("decision-submit"));
    await waitFor(() => expect(decide).toHaveBeenCalledTimes(1));
    expect((decide.mock.calls[0] as unknown as [{ body: unknown }])[0].body).toEqual({
      decision: "approved",
      comment: null,
      approver_id: 9002,
    });
    await waitFor(() => expect(count.mock.calls.length).toBeGreaterThanOrEqual(2));
  });

  it("Ablehnen opens the dialog on rejection and demands a reason", async () => {
    searchParams = { id: 501 };
    api();
    renderApproval(<ApprovalsPage />);
    fireEvent.click(await screen.findByTestId("approval-reject"));
    expect(await screen.findByTestId("decision-choice-rejected")).toHaveAttribute("aria-checked", "true");
  });

  it("shows rows on behalf as 'in Vertretung für Max Beispiel'", async () => {
    const page = inbox();
    page.items[0].on_behalf_of_label = "Max Beispiel";
    api({ "GET /api/v1/approvals/inbox": () => page });
    renderApproval(<ApprovalsPage />);
    const rows = await screen.findAllByTestId("approval-row");
    expect(within(rows[1]).getByTestId("approval-row-on-behalf")).toHaveTextContent("in Vertretung für Max Beispiel");
  });

  it("shows 'nicht möglich (bereits abgestimmt)' and offers no vote buttons for a blocked row", async () => {
    const page = inbox();
    page.items[0].can_vote = false;
    page.items[0].blocked_reason = "already_voted";
    searchParams = { id: 501 };
    api({ "GET /api/v1/approvals/inbox": () => page });
    renderApproval(<ApprovalsPage />);
    const rows = await screen.findAllByTestId("approval-row");
    expect(within(rows[1]).getByTestId("approval-row-blocked")).toHaveTextContent("nicht möglich (bereits abgestimmt)");
    expect(await screen.findByTestId("approval-detail-blocked")).toHaveTextContent("nicht möglich (bereits abgestimmt)");
    expect(screen.queryByTestId("approval-approve")).toBeNull();
  });

  it("shows 'nicht möglich (eigene Anfrage)' for an own request", async () => {
    const page = inbox();
    page.items[1].can_vote = false;
    page.items[1].blocked_reason = "own_request";
    api({ "GET /api/v1/approvals/inbox": () => page });
    renderApproval(<ApprovalsPage />);
    const rows = await screen.findAllByTestId("approval-row");
    expect(within(rows[0]).getByTestId("approval-row-blocked")).toHaveTextContent("nicht möglich (eigene Anfrage)");
  });

  it("renders the detail as a full-screen sheet on a phone", async () => {
    setMatchMedia(true);
    searchParams = { id: 501 };
    api();
    renderApproval(<ApprovalsPage />);
    const sheet = await screen.findByTestId("approval-sheet");
    expect(sheet).toHaveAttribute("role", "dialog");
    expect(screen.queryByTestId("approval-panel")).toBeNull();
    await within(sheet).findByTestId("approval-approve");
    fireEvent.click(screen.getByTestId("approval-sheet-close"));
    expect(navigate).toHaveBeenCalledWith({ to: "/agent/approvals", search: { tab: undefined } });
  });

  it("the Erledigt tab asks for decided requests", async () => {
    searchParams = { tab: "done" };
    const spy = api({ "GET /api/v1/approvals/inbox": (i) => demoApprovalInbox((i?.query as { state?: string } | undefined)?.state ?? null) });
    renderApproval(<ApprovalsPage />);
    const rows = await screen.findAllByTestId("approval-row");
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveAttribute("data-approval-id", "502");
    expect(spy).toHaveBeenCalledWith("GET", "/api/v1/approvals/inbox", expect.objectContaining({ query: expect.objectContaining({ state: "decided" }) }));
  });

  it("the Vertretung tab renders the delegation card", async () => {
    searchParams = { tab: "delegation" };
    api();
    renderApproval(<ApprovalsPage />);
    expect(await screen.findByTestId("delegation-card")).toBeInTheDocument();
  });
});
