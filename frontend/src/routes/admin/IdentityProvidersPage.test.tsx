import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import type { ReactNode } from "react";
import i18n from "@/i18n";
import { IdentityProvidersPage } from "./IdentityProvidersPage";
import { presets, summaryRows, makeProvider } from "@/components/admin/identity/testFixtures";

const navigate = vi.fn();
const list = vi.fn();
const listPresets = vi.fn();
const create = vi.fn();
const remove = vi.fn();

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
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
      list: (...a: unknown[]) => list(...a),
      presets: (...a: unknown[]) => listPresets(...a),
      create: (...a: unknown[]) => create(...a),
      remove: (...a: unknown[]) => remove(...a),
    },
  };
});

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <IdentityProvidersPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

beforeEach(async () => {
  vi.clearAllMocks();
  await i18n.changeLanguage("de");
  list.mockResolvedValue(summaryRows);
  listPresets.mockResolvedValue(presets);
});

describe("IdentityProvidersPage", () => {
  it("lists providers with kind, audience and state", async () => {
    renderPage();
    const row = await screen.findByTestId("identity-row-1");
    expect(within(row).getByText("Entra Büro")).toBeInTheDocument();
    expect(within(row).getByText("OpenID Connect")).toBeInTheDocument();
    expect(within(row).getByText("Agenten und Kunden")).toBeInTheDocument();
    expect(within(row).getByTestId("identity-state-1")).toHaveTextContent("Aktiv");
    expect(within(screen.getByTestId("identity-row-3")).getByText("SAML")).toBeInTheDocument();
    expect(within(screen.getByTestId("identity-row-3")).getByTestId("identity-state-3")).toHaveTextContent("Deaktiviert");
    expect(within(screen.getByTestId("identity-row-4")).getByText("LDAP / Active Directory")).toBeInTheDocument();
  });

  it("marks environment providers and offers no delete for them", async () => {
    renderPage();
    const envRow = await screen.findByTestId("identity-row-2");
    expect(within(envRow).getByTestId("identity-env-badge")).toHaveTextContent(
      "aus Umgebungsvariablen, hier nicht änderbar",
    );
    expect(within(envRow).queryByTestId("identity-delete-2")).toBeNull();
    expect(within(screen.getByTestId("identity-row-1")).getByTestId("identity-delete-1")).toBeInTheDocument();
  });

  it("deletes a provider after confirmation", async () => {
    remove.mockResolvedValue(undefined);
    renderPage();
    fireEvent.click(await screen.findByTestId("identity-delete-1"));
    fireEvent.click(await screen.findByRole("button", { name: "Löschen" }));
    await waitFor(() => expect(remove).toHaveBeenCalledWith(1));
  });

  it("opens the preset dialog from Hinzufügen", async () => {
    renderPage();
    await screen.findByTestId("identity-row-1");
    expect(screen.queryByTestId("identity-preset-dialog")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Hinzufügen/ }));
    expect(await screen.findByTestId("identity-preset-dialog")).toBeInTheDocument();
  });

  it("creates from a preset with a slug suggestion and opens the new provider", async () => {
    create.mockResolvedValue(makeProvider({ id: 9, slug: "microsoft", name: "Microsoft" }));
    renderPage();
    await screen.findByTestId("identity-row-1");
    fireEvent.click(screen.getByRole("button", { name: /Hinzufügen/ }));
    fireEvent.click(await screen.findByTestId("identity-preset-entra_id"));
    expect(screen.getByTestId("identity-preset-entra_id")).toHaveTextContent("Microsoft Entra ID");
    fireEvent.change(screen.getByTestId("identity-new-name"), { target: { value: "Microsoft" } });
    expect(screen.getByTestId("identity-new-slug")).toHaveValue("microsoft");
    fireEvent.click(screen.getByTestId("identity-new-submit"));
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith({
        preset: "entra_id",
        name: "Microsoft",
        slug: "microsoft",
        audience: "agent",
      }),
    );
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({
        to: "/admin/identity-providers/$providerId",
        params: { providerId: "9" },
      }),
    );
  });

  it("transliterates umlauts and spaces in the slug suggestion and keeps a manual slug", async () => {
    renderPage();
    await screen.findByTestId("identity-row-1");
    fireEvent.click(screen.getByRole("button", { name: /Hinzufügen/ }));
    fireEvent.click(await screen.findByTestId("identity-preset-ldap"));
    fireEvent.change(screen.getByTestId("identity-new-name"), { target: { value: "Büro Köln" } });
    expect(screen.getByTestId("identity-new-slug")).toHaveValue("buero-koeln");
    fireEvent.change(screen.getByTestId("identity-new-slug"), { target: { value: "mein-slug" } });
    fireEvent.change(screen.getByTestId("identity-new-name"), { target: { value: "Büro Köln 2" } });
    expect(screen.getByTestId("identity-new-slug")).toHaveValue("mein-slug");
  });
});
