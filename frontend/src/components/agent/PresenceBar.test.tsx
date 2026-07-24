import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { PresenceBar } from "./PresenceBar";

const { getPresence } = vi.hoisted(() => ({
  getPresence: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: { getPresence },
}));

function renderBar(selfUserId?: number) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <I18nextProvider i18n={i18n}>
        <PresenceBar ticketId={42} selfUserId={selfUserId} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("PresenceBar", () => {
  beforeEach(() => {
    getPresence.mockReset();
    void i18n.changeLanguage("en");
  });

  it("renders nothing while loading or when no one else is present", async () => {
    getPresence.mockResolvedValue([]);
    const { container } = renderBar(1);
    await new Promise((r) => setTimeout(r, 0));
    expect(container.querySelector('[data-testid="presence-bar"]')).toBeNull();
  });

  it("filters out the current user and renders chips for everyone else", async () => {
    getPresence.mockResolvedValue([
      { user_id: 1, name: "Self User", mode: "viewing" },
      { user_id: 2, name: "Ada Agent", mode: "viewing" },
      { user_id: 3, name: "Bob Beta", mode: "composing" },
    ]);
    renderBar(1);

    const bar = await screen.findByTestId("presence-bar");
    expect(bar).toBeInTheDocument();
    expect(screen.queryByTestId("presence-chip-1")).not.toBeInTheDocument();
    expect(screen.getByTestId("presence-chip-2")).toHaveTextContent("Ada Agent");
    expect(screen.getByTestId("presence-chip-2")).toHaveTextContent("viewing");
    expect(screen.getByTestId("presence-chip-3")).toHaveTextContent("Bob Beta");
    expect(screen.getByTestId("presence-chip-3")).toHaveTextContent("composing");
  });

  it("renders all entries when selfUserId is not provided", async () => {
    getPresence.mockResolvedValue([{ user_id: 5, name: "Carol", mode: "viewing" }]);
    renderBar(undefined);
    expect(await screen.findByTestId("presence-chip-5")).toBeInTheDocument();
  });
});
