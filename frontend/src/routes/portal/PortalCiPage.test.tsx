import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { PortalCiPage } from "./PortalCiPage";

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("@tanstack/react-router", () => ({
  useParams: () => ({ ciId: "7" }),
  Link: ({ children, to, search, ...rest }: { children: React.ReactNode; to: string; search?: { ci: number } }) => (
    <a href={search ? `${to}?ci=${search.ci}` : to} {...rest}>{children}</a>
  ),
}));
vi.mock("@/lib/portalCmdbApi", () => ({ portalCmdbApi: { get } }));

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <PortalCiPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const detail = {
  id: 7,
  number: "SRV-0007",
  name: "mail.example.org",
  class_name: "Server",
  deployment_state: "Produktiv",
  incident_state: "Betriebsbereit",
  incident_functionality: "operational",
  rendered: [
    { path: "hostname", label: "Hostname", type: "text", display: "mail.example.org", ref: null, children: [], portal: true },
    { path: "docs", label: "Doku", type: "url", display: "Handbuch", ref: { kind: "url", key: "javascript:alert(1)" }, children: [], portal: true },
  ],
};

beforeEach(() => {
  get.mockReset();
  void i18n.changeLanguage("de");
});

describe("PortalCiPage", () => {
  it("shows only the delivered attributes and links to a preselected ticket form", async () => {
    get.mockResolvedValue(detail);
    wrap();
    expect(await screen.findByTestId("ci-view-hostname")).toHaveTextContent("mail.example.org");
    expect(screen.queryByTestId("ci-view-secret")).not.toBeInTheDocument();
    expect(get).toHaveBeenCalledWith(7);
    const create = screen.getByTestId("portal-ci-create-ticket");
    expect(create).toHaveAttribute("href", "/portal/tickets/new?ci=7");
    expect(create).toHaveTextContent("Ticket zu diesem System erstellen");
  });

  it("never renders a javascript: link", async () => {
    get.mockResolvedValue(detail);
    wrap();
    const view = await screen.findByTestId("ci-view-docs");
    expect(view.querySelector("a")).toBeNull();
    expect(view).toHaveTextContent("Handbuch");
  });

  it("shows a not-found message on error", async () => {
    get.mockRejectedValue(new Error("404"));
    wrap();
    expect(await screen.findByTestId("portal-ci-error")).toHaveTextContent("nicht gefunden");
  });
});
