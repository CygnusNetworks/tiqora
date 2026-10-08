import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import type { ReactNode } from "react";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { IdentityProviderPage } from "./IdentityProviderPage";
import { ConnectionTab } from "@/components/admin/identity/ConnectionTab";
import { EndpointsBox } from "@/components/admin/identity/EndpointsBox";
import { capabilities, kinds, makeProvider } from "@/components/admin/identity/testFixtures";

const navigate = vi.fn();
let search: { tab?: string; test?: string } = {};
const get = vi.fn();
const getKinds = vi.fn();
const getCaps = vi.fn();
const update = vi.fn();
const refreshMetadata = vi.fn();
const startTest = vi.fn();
const testResult = vi.fn();

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
  useParams: () => ({ providerId: "1" }),
  useSearch: () => search,
  Link: ({ to, children, ...rest }: { to: string; children: ReactNode; [k: string]: unknown }) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("@/lib/identityApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/identityApi")>();
  return {
    ...actual,
    identityApi: {
      get: (...a: unknown[]) => get(...a),
      kinds: (...a: unknown[]) => getKinds(...a),
      capabilities: (...a: unknown[]) => getCaps(...a),
      update: (...a: unknown[]) => update(...a),
      refreshMetadata: (...a: unknown[]) => refreshMetadata(...a),
      startTest: (...a: unknown[]) => startTest(...a),
      testResult: (...a: unknown[]) => testResult(...a),
    },
  };
});

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>{ui}</I18nextProvider>
    </QueryClientProvider>,
  );
}

beforeEach(async () => {
  vi.clearAllMocks();
  search = {};
  await i18n.changeLanguage("de");
  get.mockResolvedValue(makeProvider());
  getKinds.mockResolvedValue(kinds);
  getCaps.mockResolvedValue(capabilities);
});

describe("IdentityProviderPage", () => {
  it("loads provider, kinds and capabilities and shows the connection tab", async () => {
    wrap(<IdentityProviderPage />);
    expect(await screen.findByTestId("identity-connection")).toBeInTheDocument();
    expect(screen.getByTestId("identity-config-issuer")).toHaveValue("https://login.example.org/v2.0");
    expect(get).toHaveBeenCalledWith(1, expect.anything());
  });

  it("opens the test tab for ?test=<id>", async () => {
    search = { test: "t-123" };
    testResult.mockResolvedValue({ status: "started", error: null, subject: null, attributes: {}, plan: null });
    wrap(<IdentityProviderPage />);
    expect(await screen.findByTestId("identity-test-tab")).toBeInTheDocument();
    await waitFor(() => expect(testResult).toHaveBeenCalledWith(1, "t-123", expect.anything()));
  });

  it("shows placeholders for the later tabs", async () => {
    search = { tab: "mapping" };
    wrap(<IdentityProviderPage />);
    expect(await screen.findByTestId("identity-tab-placeholder")).toBeInTheDocument();
  });

  it("switches tabs through the URL search", async () => {
    wrap(<IdentityProviderPage />);
    await screen.findByTestId("identity-connection");
    fireEvent.click(screen.getByRole("tab", { name: "Testanmeldung" }));
    expect(navigate).toHaveBeenCalledWith(expect.objectContaining({ search: expect.objectContaining({ tab: "test" }) }));
  });
});

describe("ConnectionTab", () => {
  const onSaved = vi.fn();
  const renderTab = (over = {}, caps = capabilities) =>
    wrap(<ConnectionTab provider={makeProvider(over)} kinds={kinds} capabilities={caps} onSaved={onSaved} />);

  it("sends only the changed keys", async () => {
    update.mockResolvedValue(makeProvider());
    renderTab();
    fireEvent.change(screen.getByTestId("identity-config-issuer"), {
      target: { value: "https://sso.example.org/v2.0" },
    });
    fireEvent.click(screen.getByTestId("identity-save"));
    await waitFor(() => expect(update).toHaveBeenCalledWith(1, { config: { issuer: "https://sso.example.org/v2.0" } }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
  });

  it("sends common fields at top level and leaves an untouched secret out", async () => {
    update.mockResolvedValue(makeProvider());
    renderTab();
    fireEvent.change(screen.getByTestId("identity-common-name"), { target: { value: "Entra neu" } });
    fireEvent.click(screen.getByTestId("identity-save"));
    await waitFor(() => expect(update).toHaveBeenCalledWith(1, { name: "Entra neu" }));
  });

  it("sends a new secret as a string", async () => {
    update.mockResolvedValue(makeProvider());
    renderTab();
    fireEvent.change(screen.getByTestId("identity-config-client_secret"), { target: { value: "s3cret" } });
    fireEvent.click(screen.getByTestId("identity-save"));
    await waitFor(() => expect(update).toHaveBeenCalledWith(1, { config: { client_secret: "s3cret" } }));
  });

  it("maps a 422 onto the slug and config fields", async () => {
    update.mockRejectedValue(
      new ApiError(422, { detail: { message: "Bitte die markierten Felder prüfen.", errors: { slug: "Kurzname vergeben.", "config.issuer": "Muss https sein." } } }, "/x"),
    );
    renderTab();
    fireEvent.change(screen.getByTestId("identity-config-issuer"), { target: { value: "http://x" } });
    fireEvent.click(screen.getByTestId("identity-save"));
    expect(await screen.findByTestId("identity-common-slug-error")).toHaveTextContent("Kurzname vergeben.");
    expect(screen.getByTestId("identity-config-issuer-error")).toHaveTextContent("Muss https sein.");
    expect(screen.getByTestId("identity-save-error")).toHaveTextContent("Bitte die markierten Felder prüfen.");
  });

  it("disables everything for an environment provider", () => {
    renderTab({ source: "env", read_only: true });
    expect(screen.getByTestId("identity-env-badge")).toHaveTextContent("aus Umgebungsvariablen, hier nicht änderbar");
    expect(screen.getByTestId("identity-config-issuer")).toBeDisabled();
    expect(screen.getByTestId("identity-common-name")).toBeDisabled();
    expect(screen.queryByTestId("identity-save")).toBeNull();
  });

  it("shows the SAML certificate read-only with a copy button and never the private key", () => {
    renderTab({
      kind: "saml",
      config: { idp_metadata_url: "", allow_sha1_signatures: false, sp_certificate: "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----" },
      secrets_set: ["sp_private_key"],
    });
    expect(screen.getByTestId("identity-config-sp_certificate")).toBeDisabled();
    expect(screen.getByTestId("identity-copy-certificate")).toBeInTheDocument();
    expect(screen.queryByTestId("identity-config-sp_private_key")).toBeNull();
  });

  it("clears a stored secret with an empty string", async () => {
    update.mockResolvedValue(makeProvider());
    renderTab();
    fireEvent.click(screen.getByTestId("identity-config-client_secret-clear"));
    fireEvent.click(screen.getByTestId("identity-save"));
    await waitFor(() => expect(update).toHaveBeenCalledWith(1, { config: { client_secret: "" } }));
  });

  it("turns the SHA-1 warning red once the opt-in is stored", () => {
    renderTab({ kind: "saml", config: { idp_metadata_url: "", allow_sha1_signatures: true, sp_certificate: "" }, secrets_set: [] });
    expect(screen.getByTestId("identity-sha1-warning")).toHaveClass("bg-danger/15");
  });

  it("keeps the SHA-1 warning neutral while the opt-in is off", () => {
    renderTab({ kind: "saml", config: { idp_metadata_url: "", allow_sha1_signatures: false, sp_certificate: "" }, secrets_set: [] });
    expect(screen.getByTestId("identity-sha1-warning")).not.toHaveClass("bg-danger/15");
  });

  it("re-syncs a pristine form when the provider prop changes, but not a dirty one", () => {
    const props = { kinds, capabilities, onSaved: vi.fn() };
    const qc = new QueryClient();
    const tree = (p: ReturnType<typeof makeProvider>) => (
      <QueryClientProvider client={qc}>
        <I18nextProvider i18n={i18n}>
          <ConnectionTab provider={p} {...props} />
        </I18nextProvider>
      </QueryClientProvider>
    );
    const first = makeProvider();
    const view = render(tree(first));
    view.rerender(tree({ ...first, name: "Neu benannt" }));
    expect(screen.getByTestId("identity-common-name")).toHaveValue("Neu benannt");
    fireEvent.change(screen.getByTestId("identity-common-name"), { target: { value: "Meine Eingabe" } });
    view.rerender(tree({ ...first, name: "Wieder anders" }));
    expect(screen.getByTestId("identity-common-name")).toHaveValue("Meine Eingabe");
  });

  it("warns next to the SHA-1 opt-in", () => {
    renderTab({ kind: "saml", config: { idp_metadata_url: "", allow_sha1_signatures: false, sp_certificate: "" }, secrets_set: [] });
    expect(screen.getByTestId("identity-sha1-warning")).toHaveTextContent("SHA-1");
  });

  it("warns when SAML is unavailable on the server", () => {
    renderTab(
      { kind: "saml", config: { idp_metadata_url: "", allow_sha1_signatures: false, sp_certificate: "" }, secrets_set: [] },
      { ...capabilities, saml_available: false, saml_missing: "xmlsec1" },
    );
    expect(screen.getByTestId("identity-saml-unavailable")).toHaveTextContent(i18n.t("admin.identity.saml.unavailable.xmlsec1"));
  });

  it("reloads SAML metadata and shows the metadata error", async () => {
    refreshMetadata.mockResolvedValue(makeProvider({ kind: "saml", metadata_error: "Metadaten nicht lesbar." }));
    renderTab({ kind: "saml", config: { idp_metadata_url: "https://idp.example.org/md", allow_sha1_signatures: false, sp_certificate: "" }, secrets_set: [] });
    fireEvent.click(screen.getByTestId("identity-refresh-metadata"));
    await waitFor(() => expect(refreshMetadata).toHaveBeenCalledWith(1));
    expect(await screen.findByTestId("identity-metadata-error")).toHaveTextContent("Metadaten nicht lesbar.");
  });

  it("reports a draft of the unsaved changes", () => {
    const onDraft = vi.fn();
    wrap(<ConnectionTab provider={makeProvider()} kinds={kinds} capabilities={capabilities} onSaved={onSaved} onDraftChange={onDraft} />);
    fireEvent.change(screen.getByTestId("identity-config-issuer"), { target: { value: "https://a.example.org" } });
    expect(onDraft).toHaveBeenLastCalledWith({ config: { issuer: "https://a.example.org" } });
  });
});

describe("EndpointsBox", () => {
  it("lists the redirect URIs of an OIDC provider for its audience", () => {
    wrap(<EndpointsBox provider={makeProvider({ audience: "agent" })} />);
    expect(screen.getByTestId("identity-endpoint-agent_redirect_uri")).toHaveTextContent("/api/v1/auth/sso/entra/callback");
    expect(screen.queryByTestId("identity-endpoint-customer_redirect_uri")).toBeNull();
  });

  it("lists ACS, metadata and logout URLs of a SAML provider", () => {
    wrap(<EndpointsBox provider={makeProvider({ kind: "saml", audience: "both" })} />);
    for (const k of ["agent_acs_url", "customer_acs_url", "sp_metadata_url", "slo_url"]) {
      expect(screen.getByTestId(`identity-endpoint-${k}`)).toBeInTheDocument();
      expect(screen.getByTestId(`identity-copy-${k}`)).toBeInTheDocument();
    }
  });

  it("tells the admin when the public base URL is missing", () => {
    wrap(<EndpointsBox provider={makeProvider({ endpoints: { ...makeProvider().endpoints, base_url_missing: true, agent_redirect_uri: null, customer_redirect_uri: null } })} />);
    expect(screen.getByTestId("identity-endpoints-base-missing")).toHaveTextContent(i18n.t("admin.identity.endpoints.baseUrlMissing"));
  });
});
