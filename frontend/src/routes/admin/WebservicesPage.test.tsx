import type { ReactNode } from "react";
import { describe, it, expect, vi, beforeEach, beforeAll } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { WebservicesPage } from "./WebservicesPage";
import { demoWebserviceList } from "@/demo/giMock";

vi.mock("@tanstack/react-router", () => ({
  Link: ({
    to,
    params,
    search,
    children,
    ...rest
  }: {
    to: string;
    params?: Record<string, string>;
    search?: Record<string, string>;
    children: ReactNode;
    [k: string]: unknown;
  }) => (
    <a
      href={Object.entries(params ?? {}).reduce((h, [k, v]) => h.replace(`$${k}`, v), to)}
      data-search={search ? JSON.stringify(search) : undefined}
      {...rest}
    >
      {children}
    </a>
  ),
}));

const list = vi.fn();
const getDaemons = vi.fn();
vi.mock("@/lib/webservicesApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/webservicesApi")>("@/lib/webservicesApi");
  return { ...actual, webservicesApi: { ...actual.webservicesApi, list: (...a: unknown[]) => list(...a) } };
});
vi.mock("@/lib/api", () => ({ api: { getDaemons: (...a: unknown[]) => getDaemons(...a) } }));

function daemon(notices: string[]) {
  return { services: [{ slug: "gi_requester", enabled: notices.length === 0, notices }] };
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <WebservicesPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("WebservicesPage", () => {
  beforeAll(async () => {
    await i18n.changeLanguage("de");
  });
  beforeEach(() => {
    list.mockReset().mockResolvedValue(demoWebserviceList);
    getDaemons.mockReset().mockResolvedValue(daemon([]));
  });

  it("renders the rows and shows the problem of an invalid webservice", async () => {
    renderPage();
    const ok = await screen.findByTestId("webservice-row-1");
    expect(ok).toHaveTextContent("ExampleCRM");
    expect(ok).toHaveTextContent("gültig");
    expect(ok).toHaveTextContent("2");
    const bad = screen.getByTestId("webservice-row-2");
    expect(bad).toHaveTextContent("ungültig");
    expect(screen.getByTestId("webservice-problem-2")).toHaveTextContent(
      "Die Konfiguration ist kein gültiges YAML (Zeile 14).",
    );
  });

  it("warns that Tiqora does not trigger the events while the daemon is off", async () => {
    getDaemons.mockResolvedValue(daemon(["admin.daemons.notices.giRequesterOff"]));
    renderPage();
    const banner = await screen.findByTestId("webservices-notice");
    expect(banner).toHaveTextContent(/Tiqora löst deshalb keine Ereignis-Aufrufe/);
    // the banner names the service; "dieser Dienst" has no referent on this page
    expect(banner).toHaveTextContent("„Ausgehende Webservice-Aufrufe“ ist ausgeschaltet");
    expect(banner).not.toHaveTextContent("dieser Dienst");
    expect(screen.getByTestId("webservices-notice-link")).toHaveAttribute("href", "/admin/daemons");
  });

  it("shows no banner when the daemon has no notices", async () => {
    renderPage();
    await screen.findByTestId("webservice-row-1");
    expect(screen.queryByTestId("webservices-notice")).not.toBeInTheDocument();
  });

  it("links the name to the detail page and keeps the table semantics", async () => {
    renderPage();
    const row = await screen.findByTestId("webservice-row-1");
    expect(row).not.toHaveAttribute("role");
    expect(row).not.toHaveAttribute("tabindex");
    const link = screen.getByRole("link", { name: "ExampleCRM" });
    expect(link).toHaveAttribute("href", "/admin/webservices/1");
    expect(link).toHaveAttribute("data-search", JSON.stringify({ tab: "overview" }));
  });

  it("explains the double-send case with the service name", async () => {
    getDaemons.mockResolvedValue(daemon(["admin.daemons.notices.giRequesterDouble"]));
    renderPage();
    const banner = await screen.findByTestId("webservices-notice");
    expect(banner).toHaveTextContent(
      "Der Dienst „Ausgehende Webservice-Aufrufe“ ist eingeschaltet, aber der Ereignis-Handler von Znuny ist noch aktiv.",
    );
  });
});
