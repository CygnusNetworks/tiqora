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
  queue_sign: {
    backend: "pgp",
    method: "detached",
    sign_key: "AB12CD34",
    encrypt: false,
    encrypt_keys: null,
  },
  modes: ["none", "sign", "encrypt", "sign_encrypt"],
  sign_default: true,
  encrypt_policy: "auto",
  blocked: null,
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
    fireEvent.click(screen.getByTestId("sec-mode-sign_encrypt"));
    await waitFor(() =>
      expect(screen.getByTestId("blocked")).toHaveTextContent("nokey@example.net"),
    );
    const missing = screen
      .getByTestId("sec-recipients")
      .querySelector('[data-status="missing"]');
    expect(missing).toHaveTextContent("nokey@example.net");
    expect(payload()).toMatchObject({ encrypt: true, sign_key: "AB12CD34" });

    // S/MIME has certificates for everybody → not blocked any more.
    fireEvent.click(screen.getByTestId("sec-backend-smime"));
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
      modes: [],
      sign_default: true,
      encrypt_policy: "off",
    });
    wrap();
    await waitFor(() => expect(api.getCryptoOptions).toHaveBeenCalled());
    expect(screen.queryByTestId("sec")).toBeNull();
    expect(payload()).toBeNull();
  });

  it("offers only the modes the queue allows, with icons", async () => {
    vi.spyOn(api, "getCryptoOptions").mockResolvedValue({ ...OPTIONS, modes: ["none", "sign"], encrypt_policy: "off" });
    wrap();
    await screen.findByTestId("sec");
    expect(screen.getByTestId("sec-mode-sign")).toHaveAttribute("aria-checked", "true");
    expect(screen.getByTestId("sec-mode-sign").querySelector("svg")).not.toBeNull();
    expect(screen.queryByTestId("sec-mode-encrypt")).toBeNull();
    expect(screen.queryByTestId("sec-mode-sign_encrypt")).toBeNull();
  });

  it("hides the control when the queue allows nothing but plain mail", async () => {
    vi.spyOn(api, "getCryptoOptions").mockResolvedValue({ ...OPTIONS, modes: ["none"], default: null });
    wrap();
    await waitFor(() => expect(api.getCryptoOptions).toHaveBeenCalled());
    expect(screen.queryByTestId("sec")).toBeNull();
    expect(payload()).toBeNull();
  });

  it("shows the backend choice only when both backends are usable", async () => {
    const pgpOnly = { ...OPTIONS, backends: [OPTIONS.backends![0], { ...OPTIONS.backends![1], available: false }] };
    vi.spyOn(api, "getCryptoOptions").mockResolvedValue(pgpOnly);
    wrap();
    await screen.findByTestId("sec");
    expect(screen.queryByTestId("sec-backend")).toBeNull();
  });

  it("preselects encryption from the queue default and locks plain modes when required", async () => {
    vi.spyOn(api, "getCryptoOptions").mockResolvedValue({
      ...OPTIONS,
      modes: ["encrypt", "sign_encrypt"],
      encrypt_policy: "required",
      default: { backend: "smime", method: "detached", sign_key: null, encrypt: true, encrypt_keys: null },
    });
    wrap();
    await screen.findByTestId("sec");
    expect(screen.getByTestId("sec-mode-encrypt")).toHaveAttribute("aria-checked", "true");
    expect(screen.queryByTestId("sec-mode-none")).toBeNull();
    expect(screen.getByTestId("sec-required")).toBeInTheDocument();
    await waitFor(() => expect(payload()).toMatchObject({ backend: "smime", encrypt: true, sign_key: null }));
  });

  it("locks a backend without a usable sign key and says why", async () => {
    const noSmimeKey = {
      ...OPTIONS,
      backends: [
        OPTIONS.backends![0],
        { ...OPTIONS.backends![1], sign_keys: [] },
      ],
    };
    vi.spyOn(api, "getCryptoOptions").mockResolvedValue(noSmimeKey);
    wrap();
    await screen.findByTestId("sec");
    const smime = screen.getByTestId("sec-backend-smime");
    expect(smime).toHaveAttribute("aria-disabled", "true");
    expect(smime).toHaveTextContent("no key");
    fireEvent.click(smime);
    expect(screen.getByTestId("sec-backend-pgp")).toHaveAttribute("aria-checked", "true");
    expect(payload()).toMatchObject({ backend: "pgp" });

    // Encrypt-only needs no sign key: S/MIME is selectable again.
    fireEvent.click(screen.getByTestId("sec-mode-encrypt"));
    expect(screen.getByTestId("sec-backend-smime")).not.toHaveAttribute("aria-disabled");
  });

  it("moves to the backend that has a sign key when the mode starts signing", async () => {
    const pgpNoKey = {
      ...OPTIONS,
      default: null,
      queue_sign: null,
      backends: [
        { ...OPTIONS.backends![0], sign_keys: [] },
        OPTIONS.backends![1],
      ],
    };
    vi.spyOn(api, "getCryptoOptions").mockResolvedValue(pgpNoKey);
    wrap();
    await screen.findByTestId("sec");
    fireEvent.click(screen.getByTestId("sec-mode-sign"));
    expect(screen.getByTestId("sec-backend-smime")).toHaveAttribute("aria-checked", "true");
    expect(payload()).toMatchObject({ backend: "smime", sign_key: "abcdef01.0" });
  });
});
