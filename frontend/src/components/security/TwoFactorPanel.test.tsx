import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { TwoFactorPanel } from "./TwoFactorPanel";
import type { TwoFactorApi } from "./twoFactorApi";

const startRegistration = vi.fn();
vi.mock("@simplewebauthn/browser", () => ({
  startRegistration: (...a: unknown[]) => startRegistration(...a),
  startAuthentication: vi.fn(),
}));

function fakeApi(): { [K in keyof TwoFactorApi]: ReturnType<typeof vi.fn> } {
  return {
    status: vi.fn().mockResolvedValue({ totp_enabled: false }),
    enroll: vi.fn().mockResolvedValue({ secret: "JBSWY3DPEHPK3PXP", otpauth_uri: "otpauth://x" }),
    enrollQrUrl: vi.fn().mockReturnValue("/api/fake/enroll/qr"),
    confirm: vi.fn().mockResolvedValue({ totp_enabled: true }),
    disable: vi.fn().mockResolvedValue({ totp_enabled: false }),
    listPasskeys: vi.fn().mockResolvedValue([]),
    registerBegin: vi.fn().mockResolvedValue({ challenge: "c" }),
    registerFinish: vi.fn().mockResolvedValue({ id: 1, name: "x" }),
    deletePasskey: vi.fn().mockResolvedValue(undefined),
    webauthnEnabled: vi.fn().mockResolvedValue(true),
  };
}

function renderPanel(api: ReturnType<typeof fakeApi>) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <TwoFactorPanel api={api as unknown as TwoFactorApi} testIdPrefix="fake" />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("TwoFactorPanel", () => {
  beforeEach(() => {
    startRegistration.mockReset();
    Object.defineProperty(window, "PublicKeyCredential", {
      configurable: true,
      value: class PublicKeyCredential {},
    });
  });

  it("enrols TOTP: QR from the api, then confirms with the code", async () => {
    const api = fakeApi();
    renderPanel(api);
    fireEvent.click(await screen.findByTestId("totp-enroll-start"));

    const qr = await screen.findByTestId("totp-qr-image");
    expect(qr).toHaveAttribute("src", "/api/fake/enroll/qr");
    expect(screen.getByTestId("totp-secret")).toHaveTextContent("JBSWY3DPEHPK3PXP");

    fireEvent.change(screen.getByTestId("totp-confirm-code"), { target: { value: "123456" } });
    await waitFor(() => expect(api.confirm).toHaveBeenCalledWith("123456"));
    await waitFor(() => expect(api.status.mock.calls.length).toBeGreaterThan(1));
  });

  it("disables TOTP with a code", async () => {
    const api = fakeApi();
    api.status.mockResolvedValue({ totp_enabled: true });
    renderPanel(api);
    await screen.findByTestId("totp-disable-form");
    fireEvent.change(screen.getByTestId("totp-disable-code"), { target: { value: "654321" } });
    await waitFor(() => expect(api.disable).toHaveBeenCalledWith("654321"));
  });

  it("lists, adds and deletes passkeys", async () => {
    const api = fakeApi();
    api.listPasskeys.mockResolvedValue([
      { id: 7, name: "YubiKey", created: "2026-01-15T10:00:00Z", last_used_at: null },
    ]);
    const credential = { id: "cred", type: "public-key" };
    startRegistration.mockResolvedValue(credential);
    renderPanel(api);

    expect(await screen.findByTestId("passkey-item-7")).toHaveTextContent("YubiKey");

    fireEvent.click(screen.getByTestId("passkey-add"));
    const input = await screen.findByTestId("confirm-dialog-input");
    fireEvent.change(input, { target: { value: "Phone" } });
    fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));
    await waitFor(() => expect(api.registerFinish).toHaveBeenCalledWith(credential, "Phone"));

    fireEvent.click(screen.getByTestId("passkey-delete-7"));
    fireEvent.click(await screen.findByTestId("confirm-dialog-confirm"));
    await waitFor(() => expect(api.deletePasskey).toHaveBeenCalledWith(7));
  });

  it("hides the passkey section when the backend has no WebAuthn", async () => {
    const api = fakeApi();
    api.webauthnEnabled.mockResolvedValue(false);
    renderPanel(api);
    await waitFor(() => expect(api.webauthnEnabled).toHaveBeenCalled());
    await screen.findByTestId("totp-enroll-start");
    expect(screen.queryByTestId("passkeys-section")).toBeNull();
  });

  it("explains a reauthentication_required answer when adding a passkey", async () => {
    const api = fakeApi();
    api.registerBegin.mockRejectedValue(new ApiError(403, "reauthentication_required", "/x"));
    renderPanel(api);
    fireEvent.click(await screen.findByTestId("passkey-add"));
    expect(await screen.findByTestId("passkey-error")).toHaveTextContent(
      i18n.t("security.passkeyReauthRequired"),
    );
  });

  it("explains a reauthentication_required answer when deleting a passkey", async () => {
    const api = fakeApi();
    api.listPasskeys.mockResolvedValue([
      { id: 7, name: "YubiKey", created: "2026-01-15T10:00:00Z", last_used_at: null },
    ]);
    api.deletePasskey.mockRejectedValue(new ApiError(403, "reauthentication_required", "/x"));
    renderPanel(api);
    fireEvent.click(await screen.findByTestId("passkey-delete-7"));
    fireEvent.click(await screen.findByTestId("confirm-dialog-confirm"));
    expect(await screen.findByTestId("passkey-error")).toHaveTextContent(
      i18n.t("security.passkeyReauthRequired"),
    );
  });

  // Adding TOTP to a passkey-only account needs a recent sign-in on both the agent
  // (/agent/security) and the customer (/portal/security) side: the 403 must not be silent.
  it.each(["security", "portal-security"])(
    "explains a reauthentication_required answer when setting up TOTP (%s)",
    async (prefix) => {
      const api = fakeApi();
      api.enroll.mockRejectedValue(new ApiError(403, "reauthentication_required", "/x"));
      const qc = new QueryClient({
        defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
      });
      render(
        <QueryClientProvider client={qc}>
          <I18nextProvider i18n={i18n}>
            <TwoFactorPanel api={api as unknown as TwoFactorApi} testIdPrefix={prefix} />
          </I18nextProvider>
        </QueryClientProvider>,
      );
      fireEvent.click(await screen.findByTestId("totp-enroll-start"));
      expect(await screen.findByTestId("totp-enroll-error")).toHaveTextContent(
        i18n.t("security.totpReauthRequired"),
      );
      expect(screen.queryByTestId("totp-qr-image")).toBeNull();
    },
  );

  it("shows a generic error when TOTP setup fails otherwise and clears it on retry", async () => {
    const api = fakeApi();
    api.enroll.mockRejectedValueOnce(new ApiError(500, "boom", "/x"));
    renderPanel(api);
    fireEvent.click(await screen.findByTestId("totp-enroll-start"));
    expect(await screen.findByTestId("totp-enroll-error")).toHaveTextContent(
      i18n.t("security.enrollError"),
    );
    fireEvent.click(screen.getByTestId("totp-enroll-start"));
    expect(await screen.findByTestId("totp-qr-image")).toHaveAttribute(
      "alt",
      i18n.t("security.totpQrAlt"),
    );
    expect(screen.queryByTestId("totp-enroll-error")).toBeNull();
  });
});
