import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { PortalCisPage } from "./PortalCisPage";

const { list } = vi.hoisted(() => ({ list: vi.fn() }));
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, to, params, ...rest }: { children: React.ReactNode; to: string; params?: Record<string, string> }) => (
    <a href={params ? to.replace("$ciId", params.ciId) : to} {...rest}>{children}</a>
  ),
}));
vi.mock("@/lib/portalCmdbApi", () => ({ portalCmdbApi: { list } }));

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <PortalCisPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const row = {
  id: 7,
  number: "SRV-0007",
  name: "mail.example.org",
  class_name: "Server",
  deployment_state: "Produktiv",
  incident_state: "Störung",
  incident_functionality: "incident",
};

beforeEach(() => {
  list.mockReset();
  void i18n.changeLanguage("de");
});

describe("PortalCisPage", () => {
  it("renders cards with name, class, number, incident badge and a detail link", async () => {
    list.mockResolvedValue({ items: [row], total: 1 });
    wrap();
    const card = await screen.findByTestId("portal-ci-7");
    expect(card).toHaveTextContent("mail.example.org");
    expect(card).toHaveTextContent("Server · SRV-0007");
    expect(card.closest("a")).toHaveAttribute("href", "/portal/config-items/7");
    expect(screen.getByTestId("ci-incident-badge")).toHaveAttribute("data-functionality", "incident");
    expect(screen.getByTestId("ci-incident-badge")).toHaveTextContent("Störung");
  });

  it("searches with the typed query", async () => {
    list.mockResolvedValue({ items: [row], total: 1 });
    wrap();
    await screen.findByTestId("portal-ci-7");
    fireEvent.change(screen.getByTestId("portal-cis-search"), { target: { value: "mail" } });
    fireEvent.click(screen.getByTestId("portal-cis-search-submit"));
    await waitFor(() => expect(list).toHaveBeenCalledWith({ q: "mail", limit: 100 }));
  });

  it("shows the empty state", async () => {
    list.mockResolvedValue({ items: [], total: 0 });
    wrap();
    expect(await screen.findByTestId("portal-cis-empty")).toHaveTextContent("Keine Geräte oder Systeme");
  });
});
