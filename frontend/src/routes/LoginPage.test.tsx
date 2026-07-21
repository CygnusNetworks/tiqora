import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { LoginPage } from "./LoginPage";

const navigate = vi.fn();
const login = vi.fn();
const verifyTotp = vi.fn();
const verifyPasskey = vi.fn();
const completeEnroll2fa = vi.fn();
const completeEnrollPasskey = vi.fn();
const authMethods = vi.fn();
const totpEnroll = vi.fn();
const spnegoLoginUrl = vi.fn(() => "/api/v1/auth/spnego");
const oidcLoginUrl = vi.fn(() => "/api/v1/auth/oidc/login");

let pending2fa = false;
let mustEnroll2fa = false;
let isAuthenticated = false;
let isLoading = false;
let searchParams: { next?: string; sso_error?: string } = {};

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
  useSearch: () => searchParams,
}));

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({
    login,
    verifyTotp,
    verifyPasskey,
    completeEnroll2fa,
    completeEnrollPasskey,
    pending2fa,
    mustEnroll2fa,
    isAuthenticated,
    isLoading,
    user: null,
    logout: vi.fn(),
    refresh: vi.fn(),
  }),
}));

vi.mock("@/lib/api", () => ({
  api: {
    authMethods: (...args: unknown[]) => authMethods(...args),
    totpEnroll: (...args: unknown[]) => totpEnroll(...args),
    spnegoLoginUrl: () => spnegoLoginUrl(),
    oidcLoginUrl: () => oidcLoginUrl(),
  },
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

vi.mock("@simplewebauthn/browser", () => ({
  startRegistration: vi.fn(),
  startAuthentication: vi.fn(),
}));

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <LoginPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("LoginPage", () => {
  beforeEach(() => {
    navigate.mockReset();
    login.mockReset();
    verifyTotp.mockReset();
    verifyPasskey.mockReset();
    completeEnroll2fa.mockReset();
    completeEnrollPasskey.mockReset();
    authMethods.mockReset();
    totpEnroll.mockReset();
    pending2fa = false;
    mustEnroll2fa = false;
    isAuthenticated = false;
    isLoading = false;
    searchParams = {};
    // jsdom has no WebAuthn by default — enable for passkey UI tests.
    Object.defineProperty(window, "PublicKeyCredential", {
      configurable: true,
      value: class PublicKeyCredential {},
    });
    authMethods.mockResolvedValue({
      password: true,
      oidc: false,
      spnego: false,
      ldap: false,
      webauthn: false,
    });
  });

  it("shows Kerberos button only when methods.spnego is true", async () => {
    authMethods.mockResolvedValue({
      password: true,
      oidc: false,
      spnego: true,
      ldap: false,
      webauthn: false,
    });
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("kerberos-login")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("sso-login")).not.toBeInTheDocument();
  });

  it("hides Kerberos button when spnego is false", async () => {
    renderPage();
    await waitFor(() => {
      expect(authMethods).toHaveBeenCalled();
    });
    expect(screen.queryByTestId("kerberos-login")).not.toBeInTheDocument();
  });

  it("shows SSO failure hint when sso_error query is set", async () => {
    searchParams = { sso_error: "1" };
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("sso-error")).toBeInTheDocument();
    });
  });

  it("renders forced enrollment step and completes on confirm", async () => {
    mustEnroll2fa = true;
    totpEnroll.mockResolvedValue({ secret: "JBSWY3DPEHPK3PXP", provisioning_uri: "otpauth://x" });
    completeEnroll2fa.mockResolvedValue(undefined);

    renderPage();

    await waitFor(() => {
      expect(totpEnroll).toHaveBeenCalled();
      expect(screen.getByTestId("must-enroll-step")).toBeInTheDocument();
    });
    expect(screen.getByTestId("must-enroll-secret")).toHaveTextContent("JBSWY3DPEHPK3PXP");
    expect(screen.getByTestId("must-enroll-hint")).toBeInTheDocument();

    fireEvent.change(screen.getByTestId("must-enroll-code"), {
      target: { value: "123456" },
    });
    fireEvent.submit(screen.getByTestId("must-enroll-form"));

    await waitFor(() => {
      expect(completeEnroll2fa).toHaveBeenCalledWith("123456");
    });
    await waitFor(() => {
      expect(navigate).toHaveBeenCalledWith({ to: "/agent" });
    });
  });

  it("offers passkey enrollment as alternative during forced enrollment when webauthn is on", async () => {
    mustEnroll2fa = true;
    authMethods.mockResolvedValue({
      password: true,
      oidc: false,
      spnego: false,
      ldap: false,
      webauthn: true,
    });
    totpEnroll.mockResolvedValue({ secret: "JBSWY3DPEHPK3PXP", provisioning_uri: "otpauth://x" });
    completeEnrollPasskey.mockResolvedValue(undefined);

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("must-enroll-passkey")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("must-enroll-passkey"));

    await waitFor(() => {
      expect(completeEnrollPasskey).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(navigate).toHaveBeenCalledWith({ to: "/agent" });
    });
  });

  it("shows passkey button on 2FA step when methods.webauthn is true", async () => {
    pending2fa = true;
    authMethods.mockResolvedValue({
      password: true,
      oidc: false,
      spnego: false,
      ldap: false,
      webauthn: true,
    });
    verifyPasskey.mockResolvedValue(undefined);

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("passkey-login")).toBeInTheDocument();
    });
    expect(screen.getByTestId("totp-form")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("passkey-login"));
    await waitFor(() => {
      expect(verifyPasskey).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(navigate).toHaveBeenCalledWith({ to: "/agent" });
    });
  });

  it("hides passkey button on 2FA step when webauthn is false", async () => {
    pending2fa = true;
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("totp-form")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("passkey-login")).not.toBeInTheDocument();
  });

  it("submits password login", async () => {
    login.mockResolvedValue(undefined);
    renderPage();
    await waitFor(() => expect(screen.getByTestId("login-form")).toBeInTheDocument());
    fireEvent.change(screen.getByTestId("login-username"), { target: { value: "agent" } });
    fireEvent.change(screen.getByTestId("login-password"), { target: { value: "secret" } });
    fireEvent.submit(screen.getByTestId("login-form"));
    await waitFor(() => expect(login).toHaveBeenCalledWith("agent", "secret"));
  });
});
