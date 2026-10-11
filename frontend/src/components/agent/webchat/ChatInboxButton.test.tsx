import { describe, it, expect, vi, beforeAll, afterAll, beforeEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import i18n from "@/i18n";
import { ChatInboxButton } from "./ChatInboxButton";
import { renderWithProviders, routeRequests, topics } from "./chatInboxTestUtils";

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

describe("ChatInboxButton", () => {
  it("badge shows waiting count and the accessible name", () => {
    renderWithProviders(<ChatInboxButton pending={2} topics={topics} />);
    expect(screen.getByTestId("chat-inbox-badge")).toHaveTextContent("2");
    expect(screen.getByRole("button", { name: "Chats – 2 wartend" })).toBeInTheDocument();
  });

  it("hidden without chat topics", () => {
    renderWithProviders(<ChatInboxButton pending={0} topics={[]} />);
    expect(screen.queryByTestId("chat-inbox-trigger")).toBeNull();
  });

  it("opens the panel and returns focus to the button on close", () => {
    renderWithProviders(<ChatInboxButton pending={0} topics={topics} />);
    const trigger = screen.getByTestId("chat-inbox-trigger");
    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(screen.getByText("close"));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(trigger).toHaveFocus();
  });

  it("announces new waiting chats politely, not the initial count", () => {
    const { rerender } = render(<ChatInboxButton pending={1} topics={topics} />);
    const live = screen.getByTestId("chat-inbox-live");
    expect(live).toHaveAttribute("aria-live", "polite");
    expect(live).toHaveTextContent("");
    act(() => rerender(<ChatInboxButton pending={2} topics={topics} />));
    expect(live).toHaveTextContent("Neuer Chat wartet. Wartende Chats: 2");
  });
});
