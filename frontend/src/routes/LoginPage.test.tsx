import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { KERBEROS_REAUTH_DELAY_S, LoginPage } from "./LoginPage";

const historyPush = vi.fn();
const login = vi.fn();
const verifyTotp = vi.fn();
const verifyPasskey = vi.fn();
const completeEnroll2fa = vi.fn();
const completeEnrollPasskey = vi.fn();
const refreshPending = vi.fn();
const authMethods = vi.fn();
const totpEnroll = vi.fn();
const spnegoLoginUrl = vi.fn(() => "/api/v1/auth/spnego");
const oidcLoginUrl = vi.fn(() => "/api/v1/auth/oidc/login");

let pending2fa = false;
let pendingFactors: { totp: boolean; passkey: boolean } | null = null;
let mustEnroll2fa = false;
let isAuthenticated = false;
let isLoading = false;
let searchParams: {
  next?: string;
  sso_error?: string;
  logged_out?: string;
  sso_step_up?: string;
} = {};

vi.mock("@tanstack/react-router", () => ({
  useRouter: () => ({ history: { push: historyPush } }),
  useSearch: () => searchParams,
}));

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({
    login,
    verifyTotp,
    verifyPasskey,
    completeEnroll2fa,
    completeEnrollPasskey,
    refreshPending,
    pending2fa,
    pendingFactors,
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
    historyPush.mockReset();
    login.mockReset();
    verifyTotp.mockReset();
    verifyPasskey.mockReset();
    completeEnroll2fa.mockReset();
    completeEnrollPasskey.mockReset();
    refreshPending.mockReset();
    refreshPending.mockResolvedValue("none");
    authMethods.mockReset();
    totpEnroll.mockReset();
    spnegoLoginUrl.mockClear();
    oidcLoginUrl.mockClear();
    localStorage.clear();
    pending2fa = false;
    pendingFactors = null;
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

  afterEach(() => {
    vi.useRealTimers();
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
    expect(screen.queryByTestId("sso-provider-env-oidc")).not.toBeInTheDocument();
  });

  // jsdom's `location.assign` is non-configurable, so swap the whole object
  // for a stub that records the redirect, and restore it afterwards.
  function stubLocationAssign(): { assign: ReturnType<typeof vi.fn>; restore: () => void } {
    const original = window.location;
    const assign = vi.fn();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...window.location, assign },
    });
    return {
      assign,
      restore: () => Object.defineProperty(window, "location", { configurable: true, value: original }),
    };
  }

  it("counts down 30s before auto re-auth of an expired SPNEGO session", async () => {
    vi.useFakeTimers();
    localStorage.setItem("tiqora-login-method", "spnego");
    authMethods.mockResolvedValue({
      password: true,
      oidc: false,
      spnego: true,
      ldap: false,
      webauthn: false,
    });
    searchParams = { next: "/agent/tickets" };
    const loc = stubLocationAssign();
    renderPage();

    // Flush authMethods → setSpnegoEnabled → countdown start.
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    // Banner appears; handshake is not started yet.
    const countdown = screen.getByTestId("kerberos-reauth-countdown");
    expect(countdown).toHaveTextContent(String(KERBEROS_REAUTH_DELAY_S));
    expect(spnegoLoginUrl).not.toHaveBeenCalled();
    expect(loc.assign).not.toHaveBeenCalled();

    // Each tick re-schedules via useEffect, so advance one second at a time.
    for (let i = 0; i < KERBEROS_REAUTH_DELAY_S - 1; i++) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1_000);
      });
    }
    expect(loc.assign).not.toHaveBeenCalled();
    expect(screen.getByTestId("kerberos-reauth-countdown")).toHaveTextContent(
      "1",
    );

    // Final second reaches 0 and starts the SPNEGO handshake.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(spnegoLoginUrl).toHaveBeenCalled();
    expect(loc.assign).toHaveBeenCalledWith("/api/v1/auth/spnego");
    loc.restore();
  });

  it("skips the Kerberos re-auth countdown when the user clicks Sign in now", async () => {
    localStorage.setItem("tiqora-login-method", "spnego");
    authMethods.mockResolvedValue({
      password: true,
      oidc: false,
      spnego: true,
      ldap: false,
      webauthn: false,
    });
    searchParams = { next: "/agent/tickets" };
    const loc = stubLocationAssign();
    renderPage();

    await screen.findByTestId("kerberos-reauth-banner");
    fireEvent.click(screen.getByTestId("kerberos-reauth-now"));
    expect(spnegoLoginUrl).toHaveBeenCalled();
    expect(loc.assign).toHaveBeenCalledWith("/api/v1/auth/spnego");
    loc.restore();
  });

  it("does NOT auto re-auth a password session to Kerberos on expiry", async () => {
    localStorage.setItem("tiqora-login-method", "password");
    authMethods.mockResolvedValue({
      password: true,
      oidc: false,
      spnego: true,
      ldap: false,
      webauthn: false,
    });
    searchParams = { next: "/agent/tickets" };
    const loc = stubLocationAssign();
    renderPage();
    // The normal form renders and the Kerberos handshake is never triggered.
    await screen.findByTestId("login-submit");
    expect(spnegoLoginUrl).not.toHaveBeenCalled();
    expect(loc.assign).not.toHaveBeenCalled();
    loc.restore();
  });

  it("hides Kerberos button when spnego is false", async () => {
    renderPage();
    await waitFor(() => {
      expect(authMethods).toHaveBeenCalled();
    });
    expect(screen.queryByTestId("kerberos-login")).not.toBeInTheDocument();
  });

  it("shows an SSO button per provider and starts its login", async () => {
    authMethods.mockResolvedValue({
      password: true,
      oidc: false,
      spnego: false,
      ldap: false,
      webauthn: false,
      providers: [
        { slug: "entra", kind: "oidc", label: "Mit Microsoft anmelden", icon: "microsoft" },
        { slug: "adfs", kind: "saml", label: "Firmenkonto", icon: "building" },
      ],
    });
    const loc = stubLocationAssign();
    renderPage();
    const btn = await screen.findByTestId("sso-provider-entra");
    expect(screen.getByTestId("sso-provider-adfs")).toBeInTheDocument();
    fireEvent.click(btn);
    expect(loc.assign).toHaveBeenCalledWith(
      "/api/v1/auth/sso/entra/login?next=%2F",
    );
    expect(localStorage.getItem("tiqora-login-method")).toBe("oidc");
    loc.restore();
  });

  it("passes a safe next target on to the provider login", async () => {
    authMethods.mockResolvedValue({
      password: true,
      oidc: false,
      spnego: false,
      ldap: false,
      webauthn: false,
      providers: [{ slug: "entra", kind: "oidc", label: "x", icon: null }],
    });
    searchParams = { next: "/agent/tickets?x=1" };
    const loc = stubLocationAssign();
    renderPage();
    fireEvent.click(await screen.findByTestId("sso-provider-entra"));
    expect(loc.assign).toHaveBeenCalledWith(
      "/api/v1/auth/sso/entra/login?next=%2Fagent%2Ftickets%3Fx%3D1",
    );
    loc.restore();
  });

  it("legacy oidc flag with the env-oidc provider shows exactly one SSO button", async () => {
    authMethods.mockResolvedValue({
      password: true,
      oidc: true,
      spnego: false,
      ldap: false,
      webauthn: false,
      providers: [{ slug: "env-oidc", kind: "oidc", label: "", icon: null }],
    });
    renderPage();
    const btn = await screen.findByTestId("sso-provider-env-oidc");
    expect(btn).toHaveTextContent(i18n.t("auth.providers.defaultLabel"));
    expect(screen.queryByTestId("sso-login")).not.toBeInTheDocument();
    expect(screen.getAllByTestId(/^sso-provider-/)).toHaveLength(1);
  });

  it("maps a backend sso_error code to its German message", async () => {
    searchParams = { sso_error: "no_match" };
    renderPage();
    const el = await screen.findByTestId("sso-error");
    expect(el).toHaveTextContent(i18n.t("auth.ssoErrors.no_match"));
    expect(el).not.toHaveTextContent(i18n.t("auth.ssoFailed"));
  });

  it("treats an unknown sso_error as a generic failure", async () => {
    searchParams = { sso_error: "whatever" };
    renderPage();
    expect(await screen.findByTestId("sso-error")).toHaveTextContent(
      i18n.t("auth.ssoFailed"),
    );
  });

  it("does not auto-start Kerberos after any sso_error", async () => {
    vi.useFakeTimers();
    localStorage.setItem("tiqora-login-method", "spnego");
    authMethods.mockResolvedValue({
      password: true,
      oidc: false,
      spnego: true,
      ldap: false,
      webauthn: false,
      providers: [],
    });
    searchParams = { next: "/agent/tickets", sso_error: "no_match" };
    renderPage();
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.queryByTestId("kerberos-reauth-banner")).not.toBeInTheDocument();
  });

  it("shows a note after logout", async () => {
    searchParams = { logged_out: "1" };
    renderPage();
    expect(await screen.findByTestId("logged-out-note")).toHaveTextContent(
      i18n.t("auth.loggedOut"),
    );
  });

  it("shows SSO failure hint when sso_error query is set", async () => {
    searchParams = { sso_error: "1" };
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("sso-error")).toBeInTheDocument();
    });
  });

  it("with sso_step_up shows the TOTP step from the pending state", async () => {
    searchParams = { sso_step_up: "1" };
    pending2fa = true;
    pendingFactors = { totp: true, passkey: false };
    refreshPending.mockResolvedValue("pending_2fa");
    renderPage();
    expect(await screen.findByTestId("totp-form")).toBeInTheDocument();
    expect(screen.getByTestId("sso-step-up-hint")).toHaveTextContent(
      i18n.t("auth.stepUp.hint"),
    );
    expect(refreshPending).toHaveBeenCalledTimes(1);
  });

  it("with sso_step_up shows enrolment when must_enroll_2fa", async () => {
    searchParams = { sso_step_up: "1" };
    mustEnroll2fa = true;
    refreshPending.mockResolvedValue("must_enroll_2fa");
    totpEnroll.mockResolvedValue({
      secret: "JBSWY3DPEHPK3PXP",
      provisioning_uri: "otpauth://x",
    });
    renderPage();
    expect(await screen.findByTestId("must-enroll-step")).toBeInTheDocument();
    expect(screen.getByTestId("sso-step-up-hint")).toBeInTheDocument();
    expect(refreshPending).toHaveBeenCalledTimes(1);
  });

  it("with sso_step_up and state none shows the normal form with an expiry note", async () => {
    searchParams = { sso_step_up: "1" };
    renderPage();
    expect(await screen.findByTestId("login-form")).toBeInTheDocument();
    expect(screen.queryByTestId("sso-step-up-hint")).toBeNull();
    expect(screen.queryByTestId("totp-form")).toBeNull();
    expect(await screen.findByTestId("sso-step-up-expired")).toHaveTextContent(
      i18n.t("auth.stepUp.expired"),
    );
    expect(refreshPending).toHaveBeenCalledTimes(1);
  });

  it("never starts the Kerberos countdown on a step-up return", async () => {
    // Stale "spnego" marker + SPNEGO on + next: /auth/pending has not answered yet.
    localStorage.setItem("tiqora-login-method", "spnego");
    authMethods.mockResolvedValue({
      password: true,
      oidc: false,
      spnego: true,
      ldap: false,
      webauthn: false,
    });
    refreshPending.mockReturnValue(new Promise(() => {}));
    searchParams = { sso_step_up: "1", next: "/agent/tickets" };
    const loc = stubLocationAssign();
    renderPage();
    await waitFor(() => expect(authMethods).toHaveBeenCalled());
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.queryByTestId("kerberos-reauth-banner")).toBeNull();
    expect(loc.assign).not.toHaveBeenCalled();
    loc.restore();
  });

  it("cancels a running Kerberos countdown once a second-factor step appears", async () => {
    vi.useFakeTimers();
    localStorage.setItem("tiqora-login-method", "spnego");
    authMethods.mockResolvedValue({
      password: true,
      oidc: false,
      spnego: true,
      ldap: false,
      webauthn: false,
    });
    searchParams = { next: "/agent/tickets" };
    const loc = stubLocationAssign();
    const view = renderPage();
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByTestId("kerberos-reauth-countdown")).toBeInTheDocument();
    pending2fa = true;
    pendingFactors = { totp: true, passkey: false };
    view.rerender(
      <QueryClientProvider client={new QueryClient()}>
        <I18nextProvider i18n={i18n}>
          <LoginPage />
        </I18nextProvider>
      </QueryClientProvider>,
    );
    for (let i = 0; i < KERBEROS_REAUTH_DELAY_S + 1; i++) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1_000);
      });
    }
    expect(loc.assign).not.toHaveBeenCalled();
    loc.restore();
  });

  it("does not ask for a pending login without sso_step_up", async () => {
    renderPage();
    expect(await screen.findByTestId("login-form")).toBeInTheDocument();
    expect(refreshPending).not.toHaveBeenCalled();
  });

  it("renders forced enrollment step and completes on confirm", async () => {
    mustEnroll2fa = true;
    totpEnroll.mockResolvedValue({
      secret: "JBSWY3DPEHPK3PXP",
      provisioning_uri: "otpauth://x",
    });
    completeEnroll2fa.mockResolvedValue(undefined);

    renderPage();

    await waitFor(() => {
      expect(totpEnroll).toHaveBeenCalled();
      expect(screen.getByTestId("must-enroll-step")).toBeInTheDocument();
    });
    expect(screen.getByTestId("must-enroll-secret")).toHaveTextContent(
      "JBSWY3DPEHPK3PXP",
    );
    expect(screen.getByTestId("must-enroll-hint")).toBeInTheDocument();

    fireEvent.change(screen.getByTestId("must-enroll-code"), {
      target: { value: "123456" },
    });
    fireEvent.submit(screen.getByTestId("must-enroll-form"));

    await waitFor(() => {
      expect(completeEnroll2fa).toHaveBeenCalledWith("123456");
    });
    await waitFor(() => {
      expect(historyPush).toHaveBeenCalledWith("/agent");
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
    totpEnroll.mockResolvedValue({
      secret: "JBSWY3DPEHPK3PXP",
      provisioning_uri: "otpauth://x",
    });
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
      expect(historyPush).toHaveBeenCalledWith("/agent");
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
      expect(historyPush).toHaveBeenCalledWith("/agent");
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

  it("offers only TOTP when the agent has no passkey enrolled", async () => {
    pending2fa = true;
    pendingFactors = { totp: true, passkey: false };
    authMethods.mockResolvedValue({
      password: true,
      oidc: false,
      spnego: false,
      ldap: false,
      webauthn: true,
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("totp-form")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("passkey-login")).not.toBeInTheDocument();
  });

  it("offers only the passkey when the agent has no TOTP enrolled", async () => {
    pending2fa = true;
    pendingFactors = { totp: false, passkey: true };
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
    expect(screen.queryByTestId("totp-form")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("passkey-login"));
    await waitFor(() => {
      expect(verifyPasskey).toHaveBeenCalled();
    });
  });

  it("submits password login", async () => {
    login.mockResolvedValue(undefined);
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId("login-form")).toBeInTheDocument(),
    );
    fireEvent.change(screen.getByTestId("login-username"), {
      target: { value: "agent" },
    });
    fireEvent.change(screen.getByTestId("login-password"), {
      target: { value: "secret" },
    });
    fireEvent.submit(screen.getByTestId("login-form"));
    await waitFor(() => expect(login).toHaveBeenCalledWith("agent", "secret"));
  });

  it("rejects protocol-relative next=//evil.com and falls back to /agent", async () => {
    isAuthenticated = true;
    isLoading = false;
    searchParams = { next: "//evil.com" };
    renderPage();
    await waitFor(() => {
      expect(historyPush).toHaveBeenCalledWith("/agent");
    });
    expect(historyPush).not.toHaveBeenCalledWith("//evil.com");
  });

  it("rejects next=/\\evil.com and falls back to /agent", async () => {
    isAuthenticated = true;
    isLoading = false;
    searchParams = { next: "/\\evil.com" };
    renderPage();
    await waitFor(() => {
      expect(historyPush).toHaveBeenCalledWith("/agent");
    });
    expect(historyPush).not.toHaveBeenCalledWith("/\\evil.com");
  });

  it("honors same-site next=/agent/tickets after auth", async () => {
    isAuthenticated = true;
    isLoading = false;
    searchParams = { next: "/agent/tickets" };
    renderPage();
    await waitFor(() => {
      expect(historyPush).toHaveBeenCalledWith("/agent/tickets");
    });
  });

  it("restores the query string of a same-site next after auth", async () => {
    isAuthenticated = true;
    isLoading = false;
    searchParams = { next: "/agent/dial?number=%2B49171&ticket=42" };
    renderPage();
    await waitFor(() => {
      expect(historyPush).toHaveBeenCalledWith("/agent/dial?number=%2B49171&ticket=42");
    });
  });

  it("still rejects an external next with a query", async () => {
    isAuthenticated = true;
    isLoading = false;
    searchParams = { next: "https://evil.com/agent?x=1" };
    renderPage();
    await waitFor(() => {
      expect(historyPush).toHaveBeenCalledWith("/agent");
    });
    expect(historyPush).not.toHaveBeenCalledWith("https://evil.com/agent?x=1");
  });
});
