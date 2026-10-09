import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { TicketListPage } from "./TicketListPage";

const { listStartable, listTickets } = vi.hoisted(() => ({
  listStartable: vi.fn(),
  listTickets: vi.fn(),
}));

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => vi.fn(),
  useSearch: () => ({}),
  Link: ({ children, to }: { children: React.ReactNode; to: string }) => <a href={to}>{children}</a>,
}));
vi.mock("@/lib/portalProcessApi", () => ({ portalProcessApi: { listStartable } }));
vi.mock("@/lib/portalApi", () => ({ portalApi: { portalListTickets: listTickets } }));

function wrap() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        <TicketListPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  listStartable.mockReset();
  listTickets.mockReset();
  listTickets.mockResolvedValue({ items: [], total: 0, offset: 0, limit: 100 });
  void i18n.changeLanguage("de");
});

describe("portal TicketListPage process link", () => {
  it("links to the process start page when processes are startable", async () => {
    listStartable.mockResolvedValue([{ id: 1, entity_id: "P", name: "Bestellung" }]);
    wrap();

    const link = (await screen.findByTestId("portal-new-process")).closest("a");
    expect(link).toHaveAttribute("href", "/portal/process/new");
    expect(link).toHaveTextContent("Prozess starten");
  });

  it("hides the link when nothing is startable", async () => {
    listStartable.mockResolvedValue([]);
    wrap();

    await screen.findByTestId("portal-new-ticket-link");
    await vi.waitFor(() => expect(listStartable).toHaveBeenCalled());
    expect(screen.queryByTestId("portal-new-process")).not.toBeInTheDocument();
  });

  it("hides the link when the process list cannot be loaded", async () => {
    listStartable.mockRejectedValue(new Error("boom"));
    wrap();

    await screen.findByTestId("portal-new-ticket-link");
    await vi.waitFor(() => expect(listStartable).toHaveBeenCalled());
    expect(screen.queryByTestId("portal-new-process")).not.toBeInTheDocument();
  });
});
