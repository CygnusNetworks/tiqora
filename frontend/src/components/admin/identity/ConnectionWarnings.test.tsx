import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { ConnectionTab } from "./ConnectionTab";
import { capabilities, kinds, makeProvider } from "./testFixtures";

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>{ui}</I18nextProvider>
    </QueryClientProvider>,
  );
}

const ldapConfig = { host: "ldap.example.org", use_ssl: true, use_starttls: false, tls_verify: false };

beforeEach(async () => {
  await i18n.changeLanguage("de");
});

describe("connection warnings", () => {
  it("warns when a database LDAP provider does not verify the server certificate, and follows the checkbox", () => {
    wrap(
      <ConnectionTab provider={makeProvider({ kind: "ldap", config: ldapConfig })} kinds={kinds} capabilities={capabilities} onSaved={vi.fn()} />,
    );
    expect(screen.getByTestId("identity-ldap-tls-warning")).toHaveTextContent("Zertifikat des Verzeichnisservers wird nicht geprüft");
    fireEvent.click(screen.getByTestId("identity-config-tls_verify"));
    expect(screen.queryByTestId("identity-ldap-tls-warning")).toBeNull();
  });

  it("does not warn when the certificate is verified", () => {
    wrap(
      <ConnectionTab
        provider={makeProvider({ kind: "ldap", config: { ...ldapConfig, tls_verify: true } })}
        kinds={kinds}
        capabilities={capabilities}
        onSaved={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("identity-ldap-tls-warning")).toBeNull();
  });

  it("warns for environment LDAP with verification off and names the environment variable", () => {
    wrap(
      <ConnectionTab
        provider={makeProvider({ kind: "ldap", source: "env", read_only: true, config: ldapConfig })}
        kinds={kinds}
        capabilities={capabilities}
        onSaved={vi.fn()}
      />,
    );
    expect(screen.getByTestId("identity-ldap-tls-warning")).toHaveTextContent("TIQORA_LDAP_TLS_VERIFY=true");
  });

  it("shows the tenant hint for Entra on the allowed tenant IDs field", () => {
    wrap(<ConnectionTab provider={makeProvider()} kinds={kinds} capabilities={capabilities} onSaved={vi.fn()} />);
    const hint = screen.getByText(/Leer = jede Organisation \(Mandant\)/);
    expect(hint).toHaveTextContent("Bei Zuordnung über den Anmeldenamen die eigene Mandanten-ID eintragen");
  });

  it("keeps showing the SHA-1 warning for SAML providers", () => {
    wrap(<ConnectionTab provider={makeProvider({ kind: "saml", config: { allow_sha1_signatures: true } })} kinds={kinds} capabilities={capabilities} onSaved={vi.fn()} />);
    expect(screen.getByTestId("identity-sha1-warning")).toHaveTextContent("SHA-1");
  });
});
