import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { SessionTab } from "./SessionTab";
import { capabilities, makeProvider } from "./testFixtures";

const update = vi.fn();
const getCapabilities = vi.fn();

vi.mock("@/lib/identityApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/identityApi")>();
  return {
    ...actual,
    identityApi: {
      update: (...a: unknown[]) => update(...a),
      capabilities: (...a: unknown[]) => getCapabilities(...a),
    },
  };
});

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>{ui}</I18nextProvider>
    </QueryClientProvider>,
  );
}

const oidc = makeProvider({ id: 5, kind: "oidc", require_mfa_claim: true });

beforeEach(async () => {
  vi.clearAllMocks();
  await i18n.changeLanguage("de");
  getCapabilities.mockResolvedValue(capabilities);
  update.mockImplementation(async (_id: number, body: object) => ({ ...oidc, ...body }));
});

describe("SessionTab", () => {
  it("sends only changed policy fields", async () => {
    const onSaved = vi.fn();
    wrap(<SessionTab provider={oidc} onSaved={onSaved} />);
    await screen.findByTestId("identity-session");
    expect(screen.getByTestId("session-save")).toBeDisabled();
    fireEvent.click(screen.getByTestId("session-fallback-reject"));
    fireEvent.click(screen.getByTestId("session-save"));
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    expect(update).toHaveBeenCalledWith(5, { mfa_policy: { fallback: "reject" } });
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
  });

  it("limits lifetime inputs to the global maximum", async () => {
    wrap(<SessionTab provider={oidc} onSaved={vi.fn()} />);
    fireEvent.click(await screen.findByTestId("session-use-global"));
    const idle = screen.getByTestId("session-idle") as HTMLInputElement;
    const abs = screen.getByTestId("session-absolute") as HTMLInputElement;
    expect(idle.max).toBe("480");
    expect(idle.min).toBe("5");
    expect(abs.max).toBe("24");
    expect(abs.min).toBe("0.25");
    fireEvent.change(idle, { target: { value: "481" } });
    expect(await screen.findByText("Zwischen 5 und 480 Minuten.")).toBeInTheDocument();
    expect(screen.getByTestId("session-save")).toBeDisabled();
    fireEvent.change(idle, { target: { value: "30" } });
    fireEvent.change(abs, { target: { value: "0.5" } });
    fireEvent.click(screen.getByTestId("session-save"));
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith(5, { session_idle_seconds: 1800, session_absolute_seconds: 1800 }),
    );
  });

  it("clears to global with null", async () => {
    const custom = makeProvider({ id: 5, kind: "oidc", session_idle_seconds: 1800, session_absolute_seconds: null });
    wrap(<SessionTab provider={custom} onSaved={vi.fn()} />);
    expect(await screen.findByTestId("session-idle")).toHaveValue(30);
    fireEvent.click(screen.getByTestId("session-use-global"));
    fireEvent.click(screen.getByTestId("session-save"));
    await waitFor(() => expect(update).toHaveBeenCalledWith(5, { session_idle_seconds: null }));
    expect(screen.queryByTestId("session-idle")).toBeNull();
  });

  it("hides OIDC fields for SAML providers", async () => {
    const saml = makeProvider({ id: 6, kind: "saml", require_mfa_claim: true });
    wrap(<SessionTab provider={saml} onSaved={vi.fn()} />);
    expect(await screen.findByTestId("session-saml-fields")).toBeInTheDocument();
    expect(screen.queryByTestId("session-oidc-fields")).toBeNull();
    expect(screen.queryByTestId("session-oidc-request-acr")).toBeNull();
  });

  it("shows OIDC fields for OIDC providers and hides policy while the switch is off", async () => {
    wrap(<SessionTab provider={oidc} onSaved={vi.fn()} />);
    expect(await screen.findByTestId("session-oidc-fields")).toBeInTheDocument();
    expect(screen.queryByTestId("session-saml-fields")).toBeNull();
    fireEvent.click(screen.getByTestId("session-require-mfa"));
    expect(screen.queryByTestId("session-mfa-policy")).toBeNull();
    fireEvent.click(screen.getByTestId("session-save"));
    await waitFor(() => expect(update).toHaveBeenCalledWith(5, { require_mfa_claim: false }));
  });

  it("offers the explicit enrolment opt-in but never selects it by default", async () => {
    wrap(<SessionTab provider={oidc} onSaved={vi.fn()} />);
    const enroll = await screen.findByTestId("session-fallback-local_second_factor_or_enroll");
    expect(enroll).not.toBeChecked();
    expect(screen.getByTestId("session-fallback-local_second_factor")).toBeChecked();
    expect(screen.getByText(/Ersteinrichtung ohne Nachweis/)).toBeInTheDocument();
  });

  it("shows server errors per field", async () => {
    update.mockRejectedValue(
      new ApiError(
        422,
        { detail: { message: "invalid", errors: { "mfa_policy.oidc_request_acr_values": "Ungültiger Wert." } } },
        "/x",
      ),
    );
    wrap(<SessionTab provider={oidc} onSaved={vi.fn()} />);
    fireEvent.change(await screen.findByTestId("session-oidc-request-acr"), { target: { value: "bad" } });
    fireEvent.click(screen.getByTestId("session-save"));
    expect(await screen.findByText("Ungültiger Wert.")).toBeInTheDocument();
    expect(screen.queryByTestId("session-save-error")).toBeNull();
  });

  it("is read-only for environment providers", async () => {
    wrap(<SessionTab provider={makeProvider({ source: "env", read_only: true })} onSaved={vi.fn()} />);
    expect(await screen.findByTestId("session-env-readonly")).toBeInTheDocument();
    expect(screen.queryByTestId("session-save")).toBeNull();
  });
});
