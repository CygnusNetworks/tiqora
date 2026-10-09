import type { ReactNode } from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { TicketZoomPage } from "./TicketZoomPage";

const { getTicket, postPresence, aclActions, ticketState, header, overflow } = vi.hoisted(() => ({
  getTicket: vi.fn(),
  postPresence: vi.fn(),
  aclActions: vi.fn(),
  ticketState: vi.fn(),
  header: vi.fn(),
  overflow: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ api: { getTicket, postPresence } }));
vi.mock("@/lib/processApi", () => ({ processApi: { aclActions, ticketState } }));
vi.mock("@/auth/AuthContext", () => ({ useAuth: () => ({ user: { id: 5 } }) }));
vi.mock("@tanstack/react-router", () => ({
  useParams: () => ({ ticketId: "7" }),
  Link: ({ children }: { children: ReactNode }) => <a>{children}</a>,
}));
vi.mock("@/lib/lastViews", () => ({ recordTicketView: vi.fn() }));

// Heavy children: only what they receive from the page matters here.
vi.mock("@/components/agent/TicketHeader", () => ({
  TicketHeader: (props: { allowedActions: ReadonlySet<string> | null; overflowItems?: ReactNode }) => {
    header(props.allowedActions);
    return <div data-testid="header-stub">{props.overflowItems}</div>;
  },
}));
vi.mock("@/components/agent/AiPanel", () => ({
  AiPanel: ({ children }: { children: (ai: object) => ReactNode }) => <>{children({})}</>,
}));
vi.mock("@/components/agent/ArticleMasterDetail", () => ({ ArticleMasterDetail: () => null }));
vi.mock("@/components/agent/HistoryTable", () => ({ HistoryTable: () => null }));
vi.mock("@/components/agent/PresenceBar", () => ({ PresenceBar: () => null }));
vi.mock("@/components/agent/SimilarTicketsChip", () => ({ SimilarTicketsChip: () => null }));
vi.mock("@/components/agent/TicketZoomOverflowMenu", () => ({
  TicketZoomOverflowItems: (props: { canStartProcess: boolean }) => {
    overflow(props.canStartProcess);
    return null;
  },
}));
vi.mock("@/components/agent/process/ProcessWidget", () => ({
  ProcessWidget: (props: { allowedActions: ReadonlySet<string> | null }) => (
    <div data-testid="process-widget-stub" data-allowed={props.allowedActions ? "set" : "null"} />
  ),
}));

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <TicketZoomPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const lastAllowed = () => header.mock.calls[header.mock.calls.length - 1][0] as ReadonlySet<string> | null;

describe("TicketZoomPage Action ACL", () => {
  beforeEach(() => {
    header.mockReset();
    overflow.mockReset();
    getTicket.mockReset().mockResolvedValue({ id: 7, tn: "1", title: "T", permissions: { ro: true } });
    postPresence.mockReset().mockResolvedValue({});
    ticketState.mockReset().mockResolvedValue({ process_entity_id: null });
    aclActions.mockReset();
  });

  it("asks GET acl-actions once for the ticket and passes the allowed set down", async () => {
    aclActions.mockResolvedValue({ allowed: ["AgentTicketNote", "AgentTicketPrint"] });
    renderPage();
    await waitFor(() =>
      expect(lastAllowed()).toEqual(new Set(["AgentTicketNote", "AgentTicketPrint"])),
    );
    expect(aclActions).toHaveBeenCalledTimes(1);
    expect(aclActions).toHaveBeenCalledWith(7, expect.anything());
    expect(screen.getByTestId("process-widget-stub")).toHaveAttribute("data-allowed", "set");
  });

  it("passes null (show everything) while the call is still loading", async () => {
    aclActions.mockReturnValue(new Promise(() => {}));
    renderPage();
    await screen.findByTestId("header-stub");
    expect(lastAllowed()).toBeNull();
    expect(screen.getByTestId("process-widget-stub")).toHaveAttribute("data-allowed", "null");
  });

  it("passes null when the call fails: the UI is never blocked on it", async () => {
    aclActions.mockRejectedValue(new Error("boom"));
    renderPage();
    await screen.findByTestId("header-stub");
    await waitFor(() => expect(aclActions).toHaveBeenCalled());
    expect(lastAllowed()).toBeNull();
  });

  it.each([
    [true, true],
    [false, false],
  ])("offers the overflow 'start process' only when can_start is true (%s)", async (canStart, expected) => {
    aclActions.mockResolvedValue({ allowed: [] });
    ticketState.mockResolvedValue({ process_entity_id: null, can_start: canStart });
    renderPage();
    await waitFor(() => expect(overflow).toHaveBeenCalledWith(expected));
    if (!expected) expect(overflow).not.toHaveBeenCalledWith(true);
  });
});
