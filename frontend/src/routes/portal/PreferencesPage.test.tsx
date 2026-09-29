import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { PreferencesPage } from "./PreferencesPage";

const api = vi.hoisted(() => ({
  portalPreferences: vi.fn(),
  portalSetLanguage: vi.fn(),
  portalChangePassword: vi.fn(),
  portalUploadPgpKey: vi.fn(),
  portalUploadSmimeCertificate: vi.fn(),
}));

vi.mock("@/lib/portalApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/portalApi")>("@/lib/portalApi");
  return { ...actual, portalApi: api };
});

vi.mock("@/auth/CustomerAuthContext", () => ({
  useCustomerAuth: () => ({ customer: { login: "carla", email: "carla@example.com" } }),
}));

function prefs(over: Record<string, unknown> = {}) {
  return {
    language: null,
    language_enabled: true,
    password_enabled: true,
    pgp_enabled: true,
    smime_enabled: false,
    keys: {
      pgp_enabled: true,
      smime_enabled: false,
      can_edit: true,
      pgp_keys: [],
      smime_certificates: [],
      pgp_key_id: null,
      smime_filename: null,
      problems: [],
    },
    ...over,
  };
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <PreferencesPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset();
  void i18n.changeLanguage("en");
});

describe("portal PreferencesPage", () => {
  it("shows language, password and the PGP upload (S/MIME off)", async () => {
    api.portalPreferences.mockResolvedValue(prefs());
    renderPage();
    expect(await screen.findByTestId("portal-preferences-page")).toBeInTheDocument();
    expect(screen.getByTestId("portal-preferences-language")).toBeInTheDocument();
    expect(screen.getByTestId("portal-preferences-password")).toBeInTheDocument();
    expect(screen.getByTestId("portal-keys-pgp-upload-file")).toBeInTheDocument();
    expect(screen.queryByTestId("portal-keys-smime")).toBeNull();
    // Customers upload; deleting is left to agents.
    expect(screen.queryByTestId("portal-keys-pgp-delete")).toBeNull();
    expect(screen.getByText(/carla@example.com/)).toBeInTheDocument();
  });

  it("hides the key section while both backends are off", async () => {
    api.portalPreferences.mockResolvedValue(prefs({ pgp_enabled: false, smime_enabled: false }));
    renderPage();
    await screen.findByTestId("portal-preferences-page");
    expect(screen.queryByTestId("portal-preferences-keys")).toBeNull();
  });

  it("checks the repeated password before calling the API", async () => {
    api.portalPreferences.mockResolvedValue(prefs());
    api.portalChangePassword.mockResolvedValue(undefined);
    renderPage();
    await screen.findByTestId("portal-preferences-password");
    const set = (id: string, v: string) =>
      fireEvent.change(screen.getByTestId(`portal-preferences-password-${id}`), {
        target: { value: v },
      });
    set("current", "old password 123");
    set("new", "new password 456");
    set("repeat", "new password 789");
    fireEvent.click(screen.getByTestId("portal-preferences-password-save"));
    expect(await screen.findByTestId("portal-preferences-password-notice")).toHaveTextContent(
      "do not match",
    );
    expect(api.portalChangePassword).not.toHaveBeenCalled();

    set("repeat", "new password 456");
    fireEvent.click(screen.getByTestId("portal-preferences-password-save"));
    await waitFor(() =>
      expect(api.portalChangePassword).toHaveBeenCalledWith("old password 123", "new password 456"),
    );
    expect(await screen.findByText("Password changed.")).toBeInTheDocument();
  });

  it("saves the language", async () => {
    api.portalPreferences.mockResolvedValue(prefs());
    api.portalSetLanguage.mockResolvedValue(prefs({ language: "en" }));
    renderPage();
    fireEvent.click(await screen.findByTestId("portal-preferences-language-save"));
    await waitFor(() => expect(api.portalSetLanguage).toHaveBeenCalledWith("en"));
    expect(await screen.findByText("Language saved.")).toBeInTheDocument();
  });
});
