import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { NewTicketPage } from "./NewTicketPage";

const { navigate, search, createTicket, list } = vi.hoisted(() => ({
  navigate: vi.fn(),
  search: { current: {} as Record<string, unknown> },
  createTicket: vi.fn(),
  list: vi.fn(),
}));
vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
  useSearch: () => search.current,
}));
vi.mock("@/lib/portalApi", () => ({
  portalApi: { portalCreateTicket: createTicket },
  ApiError: class ApiError extends Error {},
}));
vi.mock("@/lib/portalCmdbApi", () => ({ portalCmdbApi: { list } }));

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <NewTicketPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const ci = (id: number) => ({
  id,
  number: `SRV-${id}`,
  name: `host${id}.example.org`,
  class_name: "Server",
  deployment_state: "Produktiv",
  incident_state: "Betriebsbereit",
  incident_functionality: "operational",
});

function fill() {
  fireEvent.change(screen.getByTestId("portal-new-ticket-subject"), { target: { value: "Titel" } });
  fireEvent.change(screen.getByTestId("portal-new-ticket-body"), { target: { value: "Text" } });
}

beforeEach(() => {
  navigate.mockReset();
  createTicket.mockReset();
  createTicket.mockResolvedValue({ ticket_id: 5 });
  list.mockReset();
  search.current = {};
  void i18n.changeLanguage("de");
});

describe("portal NewTicketPage device picker", () => {
  it("sends the id preselected through ?ci=", async () => {
    search.current = { ci: 2 };
    list.mockResolvedValue({ items: [ci(1), ci(2)], total: 2 });
    wrap();
    const box = await screen.findByTestId("portal-new-ticket-ci-2");
    expect(box).toBeChecked();
    expect(screen.getByTestId("portal-new-ticket-ci-1")).not.toBeChecked();
    fill();
    fireEvent.click(screen.getByTestId("portal-new-ticket-submit"));
    await waitFor(() =>
      expect(createTicket).toHaveBeenCalledWith({ title: "Titel", body: "Text", config_item_ids: [2] }),
    );
  });

  it("omits config_item_ids when nothing is picked", async () => {
    list.mockResolvedValue({ items: [ci(1)], total: 1 });
    wrap();
    await screen.findByTestId("portal-new-ticket-ci-1");
    fill();
    fireEvent.click(screen.getByTestId("portal-new-ticket-submit"));
    await waitFor(() => expect(createTicket).toHaveBeenCalledWith({ title: "Titel", body: "Text" }));
  });

  it("allows at most 10 devices", async () => {
    const items = Array.from({ length: 12 }, (_, i) => ci(i + 1));
    list.mockResolvedValue({ items, total: 12 });
    wrap();
    await screen.findByTestId("portal-new-ticket-ci-1");
    for (let i = 1; i <= 10; i++) fireEvent.click(screen.getByTestId(`portal-new-ticket-ci-${i}`));
    expect(screen.getByTestId("portal-new-ticket-ci-11")).toBeDisabled();
    expect(screen.getByTestId("portal-new-ticket-ci-10")).not.toBeDisabled();
  });

  it("hides the picker when the list is empty", async () => {
    list.mockResolvedValue({ items: [], total: 0 });
    wrap();
    await waitFor(() => expect(list).toHaveBeenCalled());
    expect(screen.queryByTestId("portal-new-ticket-cis")).not.toBeInTheDocument();
  });

  it("hides the picker, drops ?ci= and still creates the ticket when the portal CMDB answers 404", async () => {
    search.current = { ci: 2 };
    list.mockRejectedValue(new Error("404"));
    wrap();
    await waitFor(() => expect(list).toHaveBeenCalled());
    expect(screen.queryByTestId("portal-new-ticket-cis")).not.toBeInTheDocument();
    fill();
    fireEvent.click(screen.getByTestId("portal-new-ticket-submit"));
    await waitFor(() => expect(createTicket).toHaveBeenCalledWith({ title: "Titel", body: "Text" }));
  });
});
