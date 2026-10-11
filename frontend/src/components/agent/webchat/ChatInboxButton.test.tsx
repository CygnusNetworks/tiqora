import { describe, it, expect, vi, beforeAll, afterAll, beforeEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import i18n from "@/i18n";
import { ChatInboxButton } from "./ChatInboxButton";
import { routeRequests, topics } from "./chatInboxTestUtils";

const { request } = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { request } };
});
vi.mock("./ChatInboxPanel", () => ({
  ChatInboxPanel: ({ onClose }: { onClose: () => void }) => (
    <div role="dialog" aria-label="Chats">
      <button onClick={onClose}>close</button>
    </div>
  ),
}));

beforeAll(() => i18n.changeLanguage("de"));
afterAll(() => i18n.changeLanguage("en"));
beforeEach(() => routeRequests(request, {}));

let ring = { pending: 0, topics, ready: true };
vi.mock("./ChatRingContext", () => ({ useChatRingState: () => ring }));

describe("ChatInboxButton", () => {
  it("badge shows waiting count and the accessible name", () => {
    ring = { pending: 2, topics, ready: true };
    render(<ChatInboxButton />);
    expect(screen.getByTestId("chat-inbox-badge-desktop")).toHaveTextContent("2");
    expect(screen.getByRole("button", { name: "Chats – 2 wartend" })).toBeInTheDocument();
  });

  it("hidden without chat topics", () => {
    ring = { pending: 0, topics: [], ready: true };
    render(<ChatInboxButton />);
    expect(screen.queryByTestId("chat-inbox-trigger-desktop")).toBeNull();
  });

  it("uses distinct test ids per header copy", () => {
    ring = { pending: 0, topics, ready: true };
    render(<><ChatInboxButton variant="mobile" /><ChatInboxButton /></>);
    expect(screen.getByTestId("chat-inbox-trigger-mobile")).toBeInTheDocument();
    expect(screen.getByTestId("chat-inbox-trigger-desktop")).toBeInTheDocument();
  });

  it("opens the panel and returns focus to the button on close", () => {
    ring = { pending: 0, topics, ready: true };
    render(<ChatInboxButton />);
    const trigger = screen.getByTestId("chat-inbox-trigger-desktop");
    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(screen.getByText("close"));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(trigger).toHaveFocus();
  });

  it("announces chats arriving after load politely, not the ones already waiting", () => {
    ring = { pending: 0, topics, ready: false };
    const { rerender } = render(<ChatInboxButton />);
    const live = screen.getByTestId("chat-inbox-live-desktop");
    expect(live).toHaveAttribute("aria-live", "polite");
    // First inbox data: 1 chat was already waiting.
    ring = { pending: 1, topics, ready: true };
    act(() => rerender(<ChatInboxButton />));
    expect(live).toHaveTextContent("");
    ring = { pending: 2, topics, ready: true };
    act(() => rerender(<ChatInboxButton />));
    expect(live).toHaveTextContent("Neuer Chat wartet. Wartende Chats: 2");
  });
});
