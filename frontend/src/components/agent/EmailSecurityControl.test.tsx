import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { api, type CryptoOptionsOut } from "@/lib/api";
import { EmailSecurityControl } from "./EmailSecurityControl";
import { useEmailSecurity } from "./useEmailSecurity";

const OPTIONS: CryptoOptionsOut = {
  enabled: true,
  from_address: "support@tiqora.test",
  default: {
    backend: "pgp",
    method: "detached",
    sign_key: "AB12CD34",
    encrypt: false,
    encrypt_keys: null,
  },
  warnings: [],
  backends: [
    {
      backend: "pgp",
      available: true,
      methods: ["detached", "inline"],
      can_encrypt: false,
      sign_keys: [
        { key: "AB12CD34", label: "AB12CD34 Support", status: "good", usable: true, emails: [] },
      ],
      recipients: [
        {
          address: "customer@example.com",
          status: "ok",
          keys: [],
          selected: ["FPR"],
        },
        { address: "nokey@example.net", status: "missing", keys: [], selected: [] },
      ],
    },
    {
      backend: "smime",
      available: true,
      methods: ["detached"],
      can_encrypt: true,
      sign_keys: [
        { key: "abcdef01.0", label: "abcdef01.0 support", status: "valid", usable: true, emails: [] },
      ],
      recipients: [
        { address: "customer@example.com", status: "ok", keys: [], selected: ["x.0"] },
        { address: "nokey@example.net", status: "ok", keys: [], selected: ["y.0"] },
      ],
    },
  ],
};

function Harness({ to }: { to: string }) {
  const security = useEmailSecurity({ ticketId: 5, to, cc: null, bcc: null });
  return (
    <div>
      <EmailSecurityControl security={security} testId="sec" />
      <output data-testid="payload">{JSON.stringify(security.payload)}</output>
      <output data-testid="blocked">{security.blocked ?? ""}</output>
    </div>
  );
}

function wrap(to = "customer@example.com, nokey@example.net") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <Harness to={to} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

beforeEach(async () => {
  await i18n.changeLanguage("en");
});

afterEach(() => {
  vi.restoreAllMocks();
});

const payload = () => JSON.parse(screen.getByTestId("payload").textContent || "null");

describe("EmailSecurityControl", () => {
  it("preselects the queue default sign key", async () => {
    const spy = vi.spyOn(api, "getCryptoOptions").mockResolvedValue(OPTIONS);
    wrap();
    await screen.findByTestId("sec");
    await waitFor(() =>
      expect(payload()).toEqual({
        backend: "pgp",
        method: "detached",
        sign_key: "AB12CD34",
        encrypt: false,
      }),
    );
    expect(screen.getByTestId("blocked")).toHaveTextContent("");
    expect(spy).toHaveBeenCalledWith(
      5,
      expect.objectContaining({ to: "customer@example.com, nokey@example.net" }),
      expect.anything(),
    );
  });

  it("blocks encryption when a recipient has no key and marks it", async () => {
    vi.spyOn(api, "getCryptoOptions").mockResolvedValue(OPTIONS);
    wrap();
    await screen.findByTestId("sec");
    fireEvent.click(screen.getByTestId("sec-mode"));
    fireEvent.click(await screen.findByTestId("sec-mode-menu-option-sign_encrypt"));
    await waitFor(() =>
      expect(screen.getByTestId("blocked")).toHaveTextContent("nokey@example.net"),
    );
    const missing = screen
      .getByTestId("sec-recipients")
      .querySelector('[data-status="missing"]');
    expect(missing).toHaveTextContent("nokey@example.net");
    expect(payload()).toMatchObject({ encrypt: true, sign_key: "AB12CD34" });

    // S/MIME has certificates for everybody → not blocked any more.
    fireEvent.click(screen.getByTestId("sec-backend"));
    fireEvent.click(await screen.findByTestId("sec-backend-menu-option-smime"));
    await waitFor(() => expect(screen.getByTestId("blocked")).toHaveTextContent(""));
    expect(payload()).toEqual({
      backend: "smime",
      method: "detached",
      sign_key: "abcdef01.0",
      encrypt: true,
    });
  });

  it("renders nothing and sends no security when crypto is off", async () => {
    vi.spyOn(api, "getCryptoOptions").mockResolvedValue({
      enabled: false,
      backends: [],
      warnings: [],
    });
    wrap();
    await waitFor(() => expect(api.getCryptoOptions).toHaveBeenCalled());
    expect(screen.queryByTestId("sec")).toBeNull();
    expect(payload()).toBeNull();
  });
});
