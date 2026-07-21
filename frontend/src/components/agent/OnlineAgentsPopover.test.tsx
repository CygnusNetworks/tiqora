import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { OnlineAgentsPopover } from "./OnlineAgentsPopover";

const { getOnlineAgents, pingOnlinePresence } = vi.hoisted(() => ({
  getOnlineAgents: vi.fn(),
  pingOnlinePresence: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("@/lib/api", () => ({
  api: {
    getOnlineAgents,
    pingOnlinePresence,
  },
}));

function renderPopover() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <I18nextProvider i18n={i18n}>
        <OnlineAgentsPopover />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("OnlineAgentsPopover", () => {
  beforeEach(() => {
    getOnlineAgents.mockReset();
    pingOnlinePresence.mockClear();
    void i18n.changeLanguage("de");
  });

  afterEach(() => {
    vi.clearAllTimers();
  });

  it("shows the count badge and lists online agents in the popover", async () => {
    getOnlineAgents.mockResolvedValue([
      {
        id: 7,
        login: "ada",
        full_name: "Ada Agent",
        avatar_url: null,
      },
      {
        id: 8,
        login: "bob",
        full_name: "Bob Beta",
        avatar_url: "https://example.com/bob.png",
      },
    ]);

    renderPopover();

    await waitFor(() => {
      expect(screen.getByTestId("online-agents-count")).toHaveTextContent("2");
    });

    fireEvent.click(screen.getByTestId("online-agents-trigger"));

    expect(screen.getByTestId("online-agents-panel")).toBeInTheDocument();
    expect(screen.getByTestId("online-agents-list")).toBeInTheDocument();
    expect(screen.getByTestId("online-agent-7")).toHaveTextContent("Ada Agent");
    expect(screen.getByTestId("online-agent-8")).toHaveTextContent("Bob Beta");
    expect(screen.getByText("Agenten online")).toBeInTheDocument();
  });

  it("shows an empty state when nobody is online", async () => {
    getOnlineAgents.mockResolvedValue([]);

    renderPopover();

    await waitFor(() => {
      expect(getOnlineAgents).toHaveBeenCalled();
    });
    expect(screen.queryByTestId("online-agents-count")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("online-agents-trigger"));

    expect(screen.getByTestId("online-agents-empty")).toHaveTextContent(
      /niemand online|Nobody is online/i,
    );
  });

  it("pings presence on mount", async () => {
    getOnlineAgents.mockResolvedValue([]);
    renderPopover();
    await waitFor(() => {
      expect(pingOnlinePresence).toHaveBeenCalled();
    });
  });
});
