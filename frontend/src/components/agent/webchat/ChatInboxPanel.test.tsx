import { describe, it, expect, vi, beforeAll, afterAll, beforeEach } from "vitest";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { ChatInboxPanel } from "./ChatInboxPanel";
import { agentState, item, renderWithProviders, routeRequests } from "./chatInboxTestUtils";

const { request, navigate } = vi.hoisted(() => ({ request: vi.fn(), navigate: vi.fn() }));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { request } };
});
vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
  Link: ({ children, params, onClick, ...rest }: Record<string, unknown> & { children: React.ReactNode; params: { ticketId: string } }) => (
    <a href={`/agent/tickets/${params.ticketId}`} onClick={onClick as never} data-testid={rest["data-testid"] as string}>
      {children}
    </a>
  ),
}));

beforeAll(() => i18n.changeLanguage("de"));
afterAll(() => i18n.changeLanguage("en"));
const accept = vi.fn();
const inbox = { waiting: [item()], mine: [item({ session_id: "s2", ticket_id: 120, status: "active", visitor_name: null })] };

beforeEach(() => {
  navigate.mockReset();
  accept.mockReset().mockResolvedValue({ ticket_id: 116 });
  routeRequests(request, { inbox: () => inbox, state: () => ({ ...agentState, available: true }), accept });
});

describe("ChatInboxPanel", () => {
  it("lists waiting and own chats", async () => {
    renderWithProviders(<ChatInboxPanel onClose={() => undefined} />);
    expect(await screen.findByTestId("chat-waiting-s1")).toHaveTextContent("Erika Musterfrau");
    expect(screen.getByTestId("chat-waiting-s1")).toHaveTextContent("Wartet seit 1 Min.");
    expect(screen.getByTestId("chat-mine-s2")).toHaveAttribute("href", "/agent/tickets/120");
    expect(screen.getByTestId("chat-mine-s2")).toHaveTextContent("Besucher");
  });

  it("accept navigates to the ticket", async () => {
    const onClose = vi.fn();
    renderWithProviders(<ChatInboxPanel onClose={onClose} />);
    fireEvent.click(await screen.findByTestId("chat-accept-s1"));
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({
        to: "/agent/tickets/$ticketId",
        params: { ticketId: "116" },
      }),
    );
    expect(accept).toHaveBeenCalledWith("s1");
    expect(onClose).toHaveBeenCalled();
  });

  it.each([
    ["at_capacity", "Sie haben bereits 3 Chats."],
    ["already_taken", "Schon angenommen"],
    ["not_waiting", "Dieser Chat wartet nicht mehr auf Annahme."],
  ])("accept conflict %s explains", async (code, text) => {
    accept.mockRejectedValueOnce(new ApiError(409, { detail: code }, "/x"));
    renderWithProviders(<ChatInboxPanel onClose={() => undefined} />);
    fireEvent.click(await screen.findByTestId("chat-accept-s1"));
    expect(await screen.findByTestId("chat-accept-error")).toHaveTextContent(text);
    expect(navigate).not.toHaveBeenCalled();
  });

  it("is a labelled dialog that takes focus and closes on Escape", async () => {
    const onClose = vi.fn();
    renderWithProviders(<ChatInboxPanel onClose={onClose} />);
    const dlg = await screen.findByRole("dialog", { name: "Chats" });
    expect(dlg).toHaveFocus();
    fireEvent.keyDown(dlg, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});
