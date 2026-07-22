import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { SecurityPage } from "./SecurityPage";

const totpStatus = vi.fn();
const totpEnroll = vi.fn();
const totpConfirm = vi.fn();
const totpDisable = vi.fn();
const authMethods = vi.fn();
const passkeyList = vi.fn();
const passkeyRegisterBegin = vi.fn();
const passkeyRegisterFinish = vi.fn();
const passkeyDelete = vi.fn();

const startRegistration = vi.fn();

vi.mock("@simplewebauthn/browser", () => ({
  startRegistration: (...args: unknown[]) => startRegistration(...args),
  startAuthentication: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: {
    totpStatus: (...args: unknown[]) => totpStatus(...args),
    totpEnroll: (...args: unknown[]) => totpEnroll(...args),
    totpConfirm: (...args: unknown[]) => totpConfirm(...args),
    totpDisable: (...args: unknown[]) => totpDisable(...args),
    authMethods: (...args: unknown[]) => authMethods(...args),
    passkeyList: (...args: unknown[]) => passkeyList(...args),
    passkeyRegisterBegin: (...args: unknown[]) => passkeyRegisterBegin(...args),
    passkeyRegisterFinish: (...args: unknown[]) => passkeyRegisterFinish(...args),
    passkeyDelete: (...args: unknown[]) => passkeyDelete(...args),
  },
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <SecurityPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("SecurityPage passkeys", () => {
  beforeEach(() => {
    totpStatus.mockReset();
    totpEnroll.mockReset();
    totpConfirm.mockReset();
    totpDisable.mockReset();
    authMethods.mockReset();
    passkeyList.mockReset();
    passkeyRegisterBegin.mockReset();
    passkeyRegisterFinish.mockReset();
    passkeyDelete.mockReset();
    startRegistration.mockReset();
    Object.defineProperty(window, "PublicKeyCredential", {
      configurable: true,
      value: class PublicKeyCredential {},
    });
    totpStatus.mockResolvedValue({ enabled: false });
    authMethods.mockResolvedValue({
      password: true,
      oidc: false,
      spnego: false,
      ldap: false,
      webauthn: true,
    });
    passkeyList.mockResolvedValue([]);
  });

  it("hides passkeys section when webauthn is disabled", async () => {
    authMethods.mockResolvedValue({
      password: true,
      oidc: false,
      spnego: false,
      ldap: false,
      webauthn: false,
    });
    renderPage();
    await waitFor(() => {
      expect(authMethods).toHaveBeenCalled();
    });
    expect(screen.queryByTestId("passkeys-section")).not.toBeInTheDocument();
  });

  it("lists passkeys and supports remove", async () => {
    passkeyList.mockResolvedValue([
      {
        id: 7,
        name: "YubiKey",
        created: "2026-01-15T10:00:00Z",
        last_used_at: "2026-03-01T12:00:00Z",
      },
    ]);
    passkeyDelete.mockResolvedValue(undefined);

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("passkey-list")).toBeInTheDocument();
    });
    expect(screen.getByTestId("passkey-item-7")).toHaveTextContent("YubiKey");

    fireEvent.click(screen.getByTestId("passkey-delete-7"));
    await screen.findByTestId("confirm-dialog");
    fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));

    await waitFor(() => {
      expect(passkeyDelete).toHaveBeenCalledWith(7);
    });
  });

  it("adds a passkey via register begin/ceremony/finish", async () => {
    const options = { challenge: "abc", rp: { id: "localhost", name: "Tiqora" } };
    const credential = { id: "cred-1", type: "public-key", response: {} };
    passkeyRegisterBegin.mockResolvedValue(options);
    startRegistration.mockResolvedValue(credential);
    passkeyRegisterFinish.mockResolvedValue({ id: 9, name: "Laptop", enabled: true });

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("passkey-add")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("passkey-add"));

    const input = await screen.findByTestId("confirm-dialog-input");
    fireEvent.change(input, { target: { value: "Laptop" } });
    fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));

    await waitFor(() => {
      expect(passkeyRegisterBegin).toHaveBeenCalled();
      expect(startRegistration).toHaveBeenCalledWith({ optionsJSON: options });
      expect(passkeyRegisterFinish).toHaveBeenCalledWith({
        credential,
        name: "Laptop",
      });
    });
  });
});
