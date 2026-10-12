import type { ReactNode } from "react";
import { describe, it, expect, vi, beforeEach, beforeAll } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { WebserviceDetailPage } from "./WebserviceDetailPage";
import { demoWebserviceDetails } from "@/demo/giMock";

const navigate = vi.fn();
const search: { tab: string } = { tab: "overview" };
vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
  useParams: () => ({ wsId: "1" }),
  useSearch: () => search,
  Link: ({ to, children, ...rest }: { to: string; children: ReactNode; [k: string]: unknown }) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}));

const get = vi.fn();
const putEgress = vi.fn();
vi.mock("@/lib/webservicesApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/webservicesApi")>("@/lib/webservicesApi");
  return {
    ...actual,
    webservicesApi: {
      ...actual.webservicesApi,
      get: (...a: unknown[]) => get(...a),
      putEgress: (...a: unknown[]) => putEgress(...a),
    },
  };
});
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { ...actual.api, getDaemons: () => Promise.resolve({ services: [] }) } };
});

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <WebserviceDetailPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("WebserviceDetailPage", () => {
  beforeAll(async () => {
    await i18n.changeLanguage("de");
  });
  beforeEach(() => {
    navigate.mockReset();
    get.mockReset().mockResolvedValue(demoWebserviceDetails[1]);
    putEgress.mockReset();
    search.tab = "overview";
  });

  it("overview shows the transport without any credential", async () => {
    renderPage();
    const tab = await screen.findByTestId("ws-tab-overview");
    expect(tab).toHaveTextContent("https://crm.example.org/api/v2");
    expect(tab).toHaveTextContent("BasicAuth");
  });

  it("overview shows the debug threshold in German", async () => {
    get.mockResolvedValue({ ...demoWebserviceDetails[1], debug_threshold: "notice" });
    renderPage();
    const tab = await screen.findByTestId("ws-tab-overview");
    expect(tab).toHaveTextContent("Protokollstufe: Hinweis");
    expect(tab).not.toHaveTextContent("notice");
  });

  it("switching the tab navigates with the tab search param", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("tab", { name: /Einstellungen/ }));
    expect(navigate).toHaveBeenCalledWith({
      to: "/admin/webservices/$wsId",
      params: { wsId: "1" },
      search: { tab: "settings" },
      replace: true,
    });
  });

  it("invokers tab shows event timing chips, condition and the not-yet-fired badge", async () => {
    search.tab = "invokers";
    renderPage();
    await screen.findByTestId("ws-tab-invokers");
    const create = screen.getByTestId("event-SyncTicket-TicketCreate");
    expect(create).toHaveTextContent("sofort");
    expect(create).not.toHaveTextContent("Bedingung");
    const update = screen.getByTestId("event-SyncTicket-TicketStateUpdate");
    expect(update).toHaveTextContent("im Hintergrund");
    expect(update).toHaveTextContent("Bedingung");
    const notFired = screen.getByTestId("event-NotifyCrm-ArticleCreate");
    expect(notFired).toHaveTextContent("wird von Tiqora noch nicht ausgelöst");
    expect(screen.getByTestId("event-SyncTicket-TicketCreate")).not.toHaveTextContent("noch nicht ausgelöst");
  });

  it("opens the dry-run dialog from an invoker", async () => {
    search.tab = "invokers";
    renderPage();
    fireEvent.click(await screen.findByTestId("invoker-dryrun-SyncTicket"));
    expect(await screen.findByTestId("dryrun-form")).toBeInTheDocument();
  });

  it("settings: shows the API's 422 message for a rejected target", async () => {
    search.tab = "settings";
    putEgress.mockRejectedValue(
      new ApiError(422, { detail: [{ msg: "Value error, Netz 127.0.0.1/32 darf nicht freigegeben werden" }] }, "/x"),
    );
    renderPage();
    const targets = await screen.findByTestId("ws-targets");
    fireEvent.change(targets, { target: { value: "127.0.0.1/32" } });
    fireEvent.click(screen.getByTestId("ws-egress-save"));
    const error = await screen.findByTestId("ws-egress-error");
    // Pydantic's "Value error, " prefix is English noise in the German UI
    expect(error.textContent).toBe("Netz 127.0.0.1/32 darf nicht freigegeben werden");
  });

  it("settings: saves the trimmed list without blank lines", async () => {
    search.tab = "settings";
    putEgress.mockResolvedValue({ allowed_targets: ["crm.example.org", "198.51.100.0/24"], allow_insecure_tls: false });
    renderPage();
    const targets = await screen.findByTestId("ws-targets");
    expect(targets).toHaveValue("crm.example.org");
    fireEvent.change(targets, { target: { value: "  crm.example.org \n\n 198.51.100.0/24  \n" } });
    fireEvent.click(screen.getByTestId("ws-egress-save"));
    await waitFor(() =>
      expect(putEgress).toHaveBeenCalledWith(1, {
        allowed_targets: ["crm.example.org", "198.51.100.0/24"],
        allow_insecure_tls: false,
      }),
    );
    expect(await screen.findByTestId("ws-egress-saved")).toBeInTheDocument();
    // the Übersicht warnings depend on egress: the detail is fetched again
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
  });

  it("settings: the insecure-TLS switch comes with its warning before saving", async () => {
    search.tab = "settings";
    renderPage();
    const sw = await screen.findByTestId("ws-insecure-tls");
    expect(screen.getByTestId("ws-tls-warning")).toHaveTextContent(/Ohne Zertifikatsprüfung/);
    expect(sw).not.toBeChecked();
    expect(screen.getByTestId("ws-egress-save")).toBeDisabled();
    fireEvent.click(sw);
    expect(sw).toBeChecked();
    expect(screen.getByTestId("ws-egress-save")).toBeEnabled();
    expect(putEgress).not.toHaveBeenCalled();
  });
});
