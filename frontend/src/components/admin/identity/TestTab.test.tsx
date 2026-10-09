import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { TestTab } from "./TestTab";
import { LoginPlanView } from "./LoginPlanView";
import { makeProvider } from "./testFixtures";
import type { LoginPlan } from "@/lib/identityApi";

const startTest = vi.fn();
const testResult = vi.fn();

vi.mock("@/lib/identityApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/identityApi")>();
  return {
    ...actual,
    identityApi: {
      startTest: (...a: unknown[]) => startTest(...a),
      testResult: (...a: unknown[]) => testResult(...a),
    },
  };
});

const assign = vi.fn();
const realLocation = window.location;

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>{ui}</I18nextProvider>
    </QueryClientProvider>,
  );
}

const basePlan: LoginPlan = {
  user_kind: "agent", decision: "login", matched_by: "login", user_id: 7, login: "idp.anna",
  reject_code: null, reject_message: null, profile: {}, profile_changes: {},
  roles_add: [], roles_remove: [], groups_add: [], groups_remove: [],
};

beforeEach(async () => {
  vi.clearAllMocks();
  await i18n.changeLanguage("de");
  Object.defineProperty(window, "location", { configurable: true, value: { ...realLocation, assign } });
});
afterEach(() => {
  Object.defineProperty(window, "location", { configurable: true, value: realLocation });
});

describe("TestTab", () => {
  it("starts a test login and navigates to the login URL", async () => {
    startTest.mockResolvedValue({ test_id: "t1", login_url: "/api/v1/auth/sso/entra/login?test=t1" });
    const draft = { config: { issuer: "https://x.example.org" } };
    wrap(<TestTab provider={makeProvider({ audience: "agent" })} draft={draft} />);
    fireEvent.click(screen.getByRole("button", { name: "Testanmeldung starten" }));
    await waitFor(() => expect(startTest).toHaveBeenCalledWith(1, { audience: "agent", draft }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/api/v1/auth/sso/entra/login?test=t1"));
  });

  it("hands the attributes of a finished test to the preview", async () => {
    testResult.mockResolvedValue({
      status: "done", error: null, subject: "s-1", attributes: { upn: ["anna@example.org"] },
      plan: { ...basePlan },
    });
    const onUse = vi.fn();
    wrap(<TestTab provider={makeProvider()} testId="t1" onUseAttributes={onUse} />);
    fireEvent.click(await screen.findByTestId("identity-test-use-sample"));
    expect(screen.getByRole("button", { name: "Werte für die Vorschau übernehmen" })).toBeInTheDocument();
    expect(onUse).toHaveBeenCalledWith({ upn: ["anna@example.org"] });
  });

  it("lets the admin choose the audience for providers serving both", async () => {
    startTest.mockResolvedValue({ test_id: "t1", login_url: "/l" });
    wrap(<TestTab provider={makeProvider({ audience: "both" })} />);
    fireEvent.click(screen.getByTestId("identity-test-audience-customer"));
    fireEvent.click(screen.getByRole("button", { name: "Testanmeldung starten" }));
    await waitFor(() => expect(startTest).toHaveBeenCalledWith(1, { audience: "customer", draft: undefined }));
  });

  it("shows an LDAP hint instead of the start button", () => {
    wrap(<TestTab provider={makeProvider({ kind: "ldap" })} />);
    expect(screen.getByTestId("identity-test-ldap-hint")).toHaveTextContent(i18n.t("admin.identity.test.ldapHint"));
    expect(screen.queryByRole("button", { name: "Testanmeldung starten" })).toBeNull();
  });

  it("polls until the test is done, then shows attributes and the login plan", async () => {
    testResult
      .mockResolvedValueOnce({ status: "started", error: null, subject: null, attributes: {}, plan: null })
      .mockResolvedValue({
        status: "done", error: null, subject: "sub-1",
        attributes: { preferred_username: "idp.anna", email: "anna@example.org" },
        plan: { ...basePlan, decision: "create", login: "idp.anna", roles_add: [{ id: 3, name: "Support" }] },
      });
    wrap(<TestTab provider={makeProvider()} testId="t1" />);
    expect(await screen.findByTestId("identity-test-attributes", {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.getByText("Vom Anmeldedienst übermittelte Werte")).toBeInTheDocument();
    expect(screen.getByText("preferred_username")).toBeInTheDocument();
    expect(screen.getByText("anna@example.org")).toBeInTheDocument();
    expect(screen.getByText("Wird beim ersten Anmelden angelegt")).toBeInTheDocument();
    expect(testResult.mock.calls.length).toBeGreaterThanOrEqual(2);
  }, 10000);

  it("maps a failed run's error code to a German message", async () => {
    testResult.mockResolvedValue({ status: "failed", error: "saml_invalid", subject: null, attributes: {}, plan: null });
    wrap(<TestTab provider={makeProvider({ kind: "saml" })} testId="t1" />);
    expect(await screen.findByTestId("identity-test-error")).toHaveTextContent(
      "Die Antwort des Anmeldedienstes war ungültig. Bitte erneut versuchen.",
    );
  });

  it("falls back to the generic message for an unknown error code", async () => {
    testResult.mockResolvedValue({ status: "failed", error: "weird", subject: null, attributes: {}, plan: null });
    wrap(<TestTab provider={makeProvider()} testId="t1" />);
    expect(await screen.findByTestId("identity-test-error")).toHaveTextContent(i18n.t("auth.ssoFailed"));
  });
});

describe("TestTab polling limits", () => {
  afterEach(() => vi.useRealTimers());

  it("stops polling after an error such as a 404", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    testResult.mockRejectedValue(new ApiError(404, "Test nicht gefunden.", "/x"));
    wrap(<TestTab provider={makeProvider()} testId="gone" />);
    await screen.findByText("Test nicht gefunden.");
    const calls = testResult.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(testResult.mock.calls.length).toBe(calls);
  });

  it("stops polling after two minutes and says so", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    testResult.mockResolvedValue({ status: "started", error: null, subject: null, attributes: {}, plan: null });
    wrap(<TestTab provider={makeProvider()} testId="slow" />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(125_000);
    });
    expect(screen.getByText(i18n.t("admin.identity.test.timeout"))).toBeInTheDocument();
    const calls = testResult.mock.calls.length;
    expect(calls).toBeGreaterThan(50);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(testResult.mock.calls.length).toBe(calls);
  });
});

describe("LoginPlanView", () => {
  it("shows the account that would be used", () => {
    wrap(<LoginPlanView plan={basePlan} />);
    expect(screen.getByText("Anmeldung als idp.anna")).toBeInTheDocument();
  });

  it("shows the reject message", () => {
    wrap(
      <LoginPlanView
        plan={{ ...basePlan, decision: "reject", login: null, reject_code: "no_match", reject_message: "Kein passendes Konto gefunden." }}
      />,
    );
    expect(screen.getByTestId("identity-plan-reject")).toHaveTextContent("Kein passendes Konto gefunden.");
  });

  it("lists role and group additions and removals", () => {
    wrap(
      <LoginPlanView
        plan={{
          ...basePlan,
          roles_add: [{ id: 3, name: "Support" }],
          roles_remove: [{ id: 4, name: "Vertrieb" }],
          groups_add: [{ group_id: 2, group_name: "users", permission_key: "rw" }],
          groups_remove: [{ group_id: 5, group_name: "admin", permission_key: "ro" }],
        }}
      />,
    );
    expect(screen.getByTestId("identity-plan-roles-add")).toHaveTextContent("Support");
    expect(screen.getByTestId("identity-plan-roles-remove")).toHaveTextContent("Vertrieb");
    expect(screen.getByTestId("identity-plan-groups-add")).toHaveTextContent("users");
    expect(screen.getByTestId("identity-plan-groups-remove")).toHaveTextContent("admin");
  });
});
