import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { SecurityPage } from "./SecurityPage";

const { request } = vi.hoisted(() => ({ request: vi.fn() }));

vi.mock("@/lib/portalApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/portalApi")>();
  return { ...actual, portalApi: { request } };
});

vi.mock("@simplewebauthn/browser", () => ({
  startRegistration: vi.fn(),
  startAuthentication: vi.fn(),
}));

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <SecurityPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("portal SecurityPage", () => {
  beforeEach(() => {
    request.mockReset();
    Object.defineProperty(window, "PublicKeyCredential", {
      configurable: true,
      value: class PublicKeyCredential {},
    });
    request.mockImplementation((method: string, path: string) => {
      if (path === "/api/portal/auth/totp/status") return Promise.resolve({ enabled: true });
      if (path === "/api/portal/auth/methods")
        return Promise.resolve({ password: true, providers: [], webauthn: true });
      if (path === "/api/portal/auth/passkey")
        return Promise.resolve([
          { id: 3, name: "Laptop", created: "2026-01-15T10:00:00Z", last_used_at: null },
        ]);
      return Promise.reject(new Error(`unexpected ${method} ${path}`));
    });
  });

  it("manages the customer's factors through the portal endpoints", async () => {
    renderPage();
    expect(screen.getByTestId("portal-security-page")).toBeInTheDocument();

    await waitFor(() =>
      expect(screen.getByTestId("totp-status-badge")).toHaveTextContent(
        i18n.t("security.totpEnabled"),
      ),
    );
    expect(await screen.findByTestId("passkey-item-3")).toHaveTextContent("Laptop");

    const paths = request.mock.calls.map((c) => c[1]);
    expect(paths).toEqual(
      expect.arrayContaining([
        "/api/portal/auth/totp/status",
        "/api/portal/auth/methods",
        "/api/portal/auth/passkey",
      ]),
    );
    expect(paths.some((p: string) => p.startsWith("/api/v1/"))).toBe(false);
  });
});
