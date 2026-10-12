import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { CustomerAuthProvider } from "@/auth/CustomerAuthContext";
import { ApiError } from "@/lib/portalApi";
import { PortalLoginPage } from "./PortalLoginPage";

const {
  navigate,
  portalMe,
  portalLogin,
  methods,
  totpVerify,
  passkeyBegin,
  passkeyFinish,
  startAuthentication,
  pendingLogin,
} = vi.hoisted(() => ({
  navigate: vi.fn(),
  portalMe: vi.fn(),
  portalLogin: vi.fn(),
  methods: vi.fn(),
  totpVerify: vi.fn(),
  passkeyBegin: vi.fn(),
  passkeyFinish: vi.fn(),
  startAuthentication: vi.fn(),
  pendingLogin: vi.fn(),
}));
let searchParams: {
  next?: string;
  sso_error?: string;
  logged_out?: string;
  sso_step_up?: string;
} = {};

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
  useSearch: () => searchParams,
}));

vi.mock("@/lib/portalApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/portalApi")>();
  return { ...actual, portalApi: { portalMe, portalLogin, portalLogout: vi.fn() } };
});

vi.mock("@/lib/identityApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/identityApi")>();
  return { ...actual, getPortalPendingLogin: (...a: unknown[]) => pendingLogin(...a) };
});

vi.mock("@/lib/portalSecurityApi", () => ({
  customerSecurityApi: {
    methods: (...a: unknown[]) => methods(...a),
    totpVerify: (...a: unknown[]) => totpVerify(...a),
    passkeyAuthenticateBegin: (...a: unknown[]) => passkeyBegin(...a),
    passkeyAuthenticateFinish: (...a: unknown[]) => passkeyFinish(...a),
  },
}));

vi.mock("@simplewebauthn/browser", () => ({
  startAuthentication: (...a: unknown[]) => startAuthentication(...a),
}));

const customer = {
  id: 5,
  login: "carla@example.org",
  email: "carla@example.org",
  customer_id: "ACME",
  first_name: "Carla",
  last_name: "Beispiel",
};

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <CustomerAuthProvider>
          <PortalLoginPage />
        </CustomerAuthProvider>
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

async function submitPassword() {
  fireEvent.change(await screen.findByTestId("portal-login-username"), {
    target: { value: "carla@example.org" },
  });
  fireEvent.change(screen.getByTestId("portal-login-password"), { target: { value: "pw" } });
  fireEvent.click(screen.getByTestId("portal-login-submit"));
}

describe("PortalLoginPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    searchParams = {};
    portalMe.mockRejectedValue(new ApiError(401, "Unauthorized", "/api/portal/auth/me"));
    methods.mockResolvedValue({ password: true, providers: [], webauthn: false });
  });

  it("with sso_step_up shows the second-factor step", async () => {
    searchParams = { sso_step_up: "1" };
    pendingLogin.mockResolvedValue({ state: "pending_2fa", totp_enrolled: true, passkey_enrolled: false });
    renderPage();
    expect(await screen.findByTestId("sso-step-up-hint")).toHaveTextContent(
      i18n.t("portal.login.stepUp.hint"),
    );
    expect(screen.queryByTestId("portal-login-form")).toBeNull();
    expect(pendingLogin).toHaveBeenCalledTimes(1);
  });

  it("with sso_step_up and state none shows the password form", async () => {
    searchParams = { sso_step_up: "1" };
    pendingLogin.mockResolvedValue({ state: "none", totp_enrolled: false, passkey_enrolled: false });
    renderPage();
    expect(await screen.findByTestId("portal-login-form")).toBeInTheDocument();
    await waitFor(() => expect(pendingLogin).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId("sso-step-up-hint")).toBeNull();
  });

  it("renders one button per identity provider linking to the portal SSO start", async () => {
    methods.mockResolvedValue({
      password: true,
      webauthn: false,
      providers: [
        { slug: "entra", kind: "oidc", label: "Mit Microsoft anmelden", icon: "microsoft" },
        { slug: "corp-saml", kind: "saml", label: "Firmenkonto", icon: null },
      ],
    });
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    renderPage();

    fireEvent.click(await screen.findByTestId("sso-provider-entra"));
    expect(assign).toHaveBeenCalledWith("/api/portal/auth/sso/entra/login?next=%2Fportal");
    expect(screen.getByTestId("sso-provider-corp-saml")).toHaveTextContent("Firmenkonto");
    vi.unstubAllGlobals();
  });

  it("shows no provider buttons when the portal offers only a password", async () => {
    renderPage();
    await screen.findByTestId("portal-login-form");
    await waitFor(() => expect(methods).toHaveBeenCalled());
    expect(screen.queryByTestId("sso-provider-entra")).toBeNull();
  });

  it("shows the message for a known sso_error code", async () => {
    searchParams = { sso_error: "no_match" };
    renderPage();
    expect(await screen.findByTestId("portal-sso-error")).toHaveTextContent(
      i18n.t("auth.ssoErrors.no_match"),
    );
  });

  it("treats any other non-empty sso_error as an error", async () => {
    searchParams = { sso_error: "1" };
    renderPage();
    expect(await screen.findByTestId("portal-sso-error")).toHaveTextContent(
      i18n.t("auth.ssoFailed"),
    );
  });

  it("shows the signed-out note after an SSO logout", async () => {
    searchParams = { logged_out: "1" };
    renderPage();
    expect(await screen.findByTestId("portal-logged-out-note")).toHaveTextContent(
      i18n.t("auth.loggedOut"),
    );
  });

  it("asks for the authenticator code after the password and then opens the portal", async () => {
    portalLogin.mockResolvedValue({ customer: null, pending_2fa: true, totp_enrolled: true });
    totpVerify.mockResolvedValue({ customer });
    portalMe.mockResolvedValue(customer);
    renderPage();
    await submitPassword();

    expect(await screen.findByTestId("portal-2fa-step")).toBeInTheDocument();
    expect(screen.queryByTestId("portal-login-form")).toBeNull();
    expect(navigate).not.toHaveBeenCalled();

    fireEvent.change(screen.getByTestId("portal-2fa-code"), { target: { value: "123456" } });
    await waitFor(() => expect(totpVerify).toHaveBeenCalledWith("123456"));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith({ to: "/portal" }));
  });

  it("reports a wrong second-factor code and stays on the step", async () => {
    portalLogin.mockResolvedValue({ customer: null, pending_2fa: true, totp_enrolled: true });
    totpVerify.mockRejectedValue(new ApiError(401, "invalid_code", "/x"));
    renderPage();
    await submitPassword();
    await screen.findByTestId("portal-2fa-step");

    fireEvent.change(screen.getByTestId("portal-2fa-code"), { target: { value: "000000" } });
    await waitFor(() => expect(totpVerify).toHaveBeenCalled());
    expect(await screen.findByText(i18n.t("portal.login.totpInvalid"))).toBeInTheDocument();
    expect(navigate).not.toHaveBeenCalled();
  });

  it("signs in directly when no second factor is pending", async () => {
    portalLogin.mockResolvedValue({ customer });
    portalMe.mockResolvedValue(customer);
    renderPage();
    await submitPassword();
    await waitFor(() => expect(navigate).toHaveBeenCalledWith({ to: "/portal" }));
  });

  it("cancel on the second-factor step returns to the password form", async () => {
    portalLogin.mockResolvedValue({ customer: null, pending_2fa: true, totp_enrolled: true });
    renderPage();
    await submitPassword();
    await screen.findByTestId("portal-2fa-step");

    fireEvent.click(screen.getByTestId("portal-2fa-cancel"));
    expect(await screen.findByTestId("portal-login-form")).toBeInTheDocument();
    expect(screen.queryByTestId("portal-2fa-step")).toBeNull();
    expect(navigate).not.toHaveBeenCalled();
  });

  it("an expired pending sign-in goes back to the password form with a message", async () => {
    portalLogin.mockResolvedValue({ customer: null, pending_2fa: true, totp_enrolled: true });
    totpVerify.mockRejectedValue(new ApiError(401, "pending_expired", "/x"));
    renderPage();
    await submitPassword();
    await screen.findByTestId("portal-2fa-step");

    fireEvent.change(screen.getByTestId("portal-2fa-code"), { target: { value: "123456" } });
    expect(await screen.findByTestId("portal-2fa-expired")).toHaveTextContent(
      i18n.t("portal.login.twoFactorExpired"),
    );
    expect(screen.getByTestId("portal-login-form")).toBeInTheDocument();
  });

  it("a wrong code (401 invalid_code) keeps the step and says the code is invalid", async () => {
    portalLogin.mockResolvedValue({ customer: null, pending_2fa: true, totp_enrolled: true });
    totpVerify.mockRejectedValue(new ApiError(401, "invalid_code", "/x"));
    renderPage();
    await submitPassword();
    await screen.findByTestId("portal-2fa-step");
    fireEvent.change(screen.getByTestId("portal-2fa-code"), { target: { value: "000000" } });
    expect(await screen.findByText(i18n.t("portal.login.totpInvalid"))).toBeInTheDocument();
    expect(screen.getByTestId("portal-2fa-step")).toBeInTheDocument();
    expect(screen.queryByTestId("portal-2fa-expired")).toBeNull();
  });

  it("a right code whose pending sign-in was lost (promotion race) goes back to the password form", async () => {
    portalLogin.mockResolvedValue({ customer: null, pending_2fa: true, totp_enrolled: true });
    totpVerify.mockRejectedValue(new ApiError(401, "pending_expired", "/x"));
    renderPage();
    await submitPassword();
    await screen.findByTestId("portal-2fa-step");
    fireEvent.change(screen.getByTestId("portal-2fa-code"), { target: { value: "123456" } });
    expect(await screen.findByTestId("portal-2fa-expired")).toBeInTheDocument();
    expect(screen.queryByTestId("portal-2fa-step")).toBeNull();
  });

  it("an unrelated 401 text is not mistaken for an expired sign-in", async () => {
    portalLogin.mockResolvedValue({ customer: null, pending_2fa: true, totp_enrolled: true });
    totpVerify.mockRejectedValue(new ApiError(401, "Session expired soon", "/x"));
    renderPage();
    await submitPassword();
    await screen.findByTestId("portal-2fa-step");
    fireEvent.change(screen.getByTestId("portal-2fa-code"), { target: { value: "000000" } });
    expect(await screen.findByText(i18n.t("portal.login.totpInvalid"))).toBeInTheDocument();
    expect(screen.queryByTestId("portal-2fa-expired")).toBeNull();
  });

  it("a rate limit (429) has its own message and keeps the step", async () => {
    portalLogin.mockResolvedValue({ customer: null, pending_2fa: true, totp_enrolled: true });
    totpVerify.mockRejectedValue(new ApiError(429, "Too many failed login attempts", "/x"));
    renderPage();
    await submitPassword();
    await screen.findByTestId("portal-2fa-step");
    fireEvent.change(screen.getByTestId("portal-2fa-code"), { target: { value: "000000" } });
    expect(await screen.findByText(i18n.t("portal.login.rateLimited"))).toBeInTheDocument();
    expect(screen.getByTestId("portal-2fa-step")).toBeInTheDocument();
  });

  describe("passkey step", () => {
    beforeEach(() => {
      portalLogin.mockResolvedValue({
        customer: null,
        pending_2fa: true,
        totp_enrolled: false,
        passkey_enrolled: true,
      });
      passkeyBegin.mockResolvedValue({ challenge: "c" });
    });

    it("completes the sign-in with a passkey", async () => {
      const credential = { id: "cred" };
      startAuthentication.mockResolvedValue(credential);
      passkeyFinish.mockResolvedValue({ customer });
      portalMe.mockResolvedValue(customer);
      renderPage();
      await submitPassword();
      fireEvent.click(await screen.findByTestId("portal-2fa-passkey"));

      await waitFor(() => expect(passkeyFinish).toHaveBeenCalledWith(credential));
      expect(startAuthentication).toHaveBeenCalledWith({ optionsJSON: { challenge: "c" } });
      await waitFor(() => expect(navigate).toHaveBeenCalledWith({ to: "/portal" }));
    });

    it("stays silent and on the step when the user cancels the browser prompt", async () => {
      const err = new Error("The operation either timed out or was not allowed");
      err.name = "NotAllowedError";
      startAuthentication.mockRejectedValue(err);
      renderPage();
      await submitPassword();
      fireEvent.click(await screen.findByTestId("portal-2fa-passkey"));

      await waitFor(() => expect(startAuthentication).toHaveBeenCalled());
      await waitFor(() => expect(screen.getByTestId("portal-2fa-passkey")).not.toBeDisabled());
      expect(screen.queryByTestId("portal-2fa-error")).toBeNull();
      expect(screen.getByTestId("portal-2fa-step")).toBeInTheDocument();
      expect(passkeyFinish).not.toHaveBeenCalled();
    });

    it("shows an error and keeps the step when the passkey is rejected", async () => {
      startAuthentication.mockResolvedValue({ id: "cred" });
      passkeyFinish.mockRejectedValue(new ApiError(401, "Passkey authentication failed", "/x"));
      renderPage();
      await submitPassword();
      fireEvent.click(await screen.findByTestId("portal-2fa-passkey"));

      expect(await screen.findByTestId("portal-2fa-error")).toHaveTextContent(
        i18n.t("portal.login.passkeyInvalid"),
      );
      expect(screen.getByTestId("portal-2fa-step")).toBeInTheDocument();
      expect(navigate).not.toHaveBeenCalled();
    });
  });
});
