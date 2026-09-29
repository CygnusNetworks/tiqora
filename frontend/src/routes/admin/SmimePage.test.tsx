import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { SmimePage } from "./SmimePage";

const status = vi.fn();
const smimeList = vi.fn();
const smimeUploadCertificate = vi.fn();
const smimeUploadPrivateKey = vi.fn();
const smimeDelete = vi.fn();
const smimeDownload = vi.fn();
const smimeRelations = vi.fn();
const smimeRelationAdd = vi.fn();
const smimeRelationDelete = vi.fn();

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    constructor(_status: number, detail: string) {
      super(detail);
    }
  },
  api: {
    adminCrypto: {
      status: (...a: unknown[]) => status(...a),
      smimeList: (...a: unknown[]) => smimeList(...a),
      smimeUploadCertificate: (...a: unknown[]) => smimeUploadCertificate(...a),
      smimeUploadPrivateKey: (...a: unknown[]) => smimeUploadPrivateKey(...a),
      smimeDelete: (...a: unknown[]) => smimeDelete(...a),
      smimeDownload: (...a: unknown[]) => smimeDownload(...a),
      smimeRelations: (...a: unknown[]) => smimeRelations(...a),
      smimeRelationAdd: (...a: unknown[]) => smimeRelationAdd(...a),
      smimeRelationDelete: (...a: unknown[]) => smimeRelationDelete(...a),
    },
  },
}));

const base = {
  valid: true,
  issuer: "CN=example root ca",
  serial: "1F",
  not_before: "2026-09-01T00:00:00Z",
  not_after: "2027-09-01T00:00:00Z",
  status: "valid",
  is_ca: false,
  has_private: false,
};
const CA_FP = "AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD";
const certs = [
  {
    ...base,
    filename: "2ea8c67d.0",
    hash: "2ea8c67d",
    subject: "CN=example root ca",
    fingerprint: CA_FP,
    emails: [],
    is_ca: true,
  },
  {
    ...base,
    filename: "d1174839.0",
    hash: "d1174839",
    subject: "CN=agent@example.org, emailAddress=agent@example.org",
    fingerprint: "11:22:33:44:55:66:77:88:99:00:AA:BB:CC:DD:EE:FF:11:22:33:44",
    emails: ["agent@example.org"],
    has_private: true,
  },
  {
    ...base,
    filename: "d1174839.1",
    hash: "d1174839",
    subject: "CN=old, emailAddress=agent@example.org",
    fingerprint: "99:22:33:44:55:66:77:88:99:00:AA:BB:CC:DD:EE:FF:11:22:33:44",
    emails: ["agent@example.org"],
    status: "expired",
  },
];

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <SmimePage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("SmimePage", () => {
  beforeEach(() => {
    for (const fn of [
      status,
      smimeList,
      smimeUploadCertificate,
      smimeUploadPrivateKey,
      smimeDelete,
      smimeDownload,
      smimeRelations,
      smimeRelationAdd,
      smimeRelationDelete,
    ])
      fn.mockReset();
    status.mockResolvedValue([]);
    smimeList.mockResolvedValue(certs);
    smimeRelations.mockResolvedValue([]);
  });

  it("lists certificates with private-key, CA and expiry status", async () => {
    renderPage();
    expect(await screen.findByText("d1174839.0")).toBeInTheDocument();
    expect(screen.getByTestId("smime-has-private-d1174839.0")).toBeInTheDocument();
    expect(screen.queryByTestId("smime-has-private-d1174839.1")).not.toBeInTheDocument();
    expect(screen.getByTestId("smime-status-d1174839.1")).toHaveTextContent(
      i18n.t("admin.smime.status.expired"),
    );
    expect(screen.getByText(i18n.t("admin.smime.ca"))).toBeInTheDocument();
  });

  it("uploads a private key with its secret", async () => {
    smimeUploadPrivateKey.mockResolvedValue({
      certificate: { ...certs[2], has_private: true },
      secret_generated: false,
    });
    renderPage();
    await screen.findByText("d1174839.0");
    fireEvent.click(screen.getByTestId("smime-key-open"));
    const pem = "-----BEGIN ENCRYPTED PRIVATE KEY-----\nabc\n-----END ENCRYPTED PRIVATE KEY-----";
    fireEvent.change(screen.getByTestId("smime-key-pem"), { target: { value: pem } });
    fireEvent.change(screen.getByTestId("smime-key-secret"), { target: { value: "geheim" } });
    fireEvent.click(screen.getByTestId("smime-key-submit"));
    await waitFor(() => expect(smimeUploadPrivateKey).toHaveBeenCalledWith(pem, "geheim"));
    expect(await screen.findByTestId("smime-notice")).toHaveTextContent("d1174839.1");
  });

  it("shows the server error when a certificate upload is refused", async () => {
    const { ApiError } = await import("@/lib/api");
    smimeUploadCertificate.mockRejectedValue(new ApiError(422, "certificate already installed as d1174839.0", "/x"));
    renderPage();
    await screen.findByText("d1174839.0");
    fireEvent.click(screen.getByTestId("smime-cert-open"));
    fireEvent.change(screen.getByTestId("smime-cert-pem"), {
      target: { value: "-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----" },
    });
    fireEvent.click(screen.getByTestId("smime-cert-submit"));
    expect(await screen.findByTestId("smime-dialog-error")).toHaveTextContent("already installed");
  });

  it("deletes a certificate after confirmation and reports renumbering", async () => {
    smimeDelete.mockResolvedValue({ renamed: { "d1174839.1": "d1174839.0" } });
    renderPage();
    await screen.findByText("d1174839.0");
    fireEvent.click(screen.getByTestId("admin-row-menu-trigger-d1174839.0"));
    fireEvent.click(await screen.findByTestId("admin-row-delete-d1174839.0"));
    fireEvent.click(await screen.findByTestId("confirm-dialog-confirm"));
    await waitFor(() => expect(smimeDelete).toHaveBeenCalledWith("d1174839.0", false));
    expect(await screen.findByTestId("smime-notice")).toHaveTextContent("d1174839.1 → d1174839.0");
  });

  it("manages signer relations for a certificate with a private key", async () => {
    smimeRelationAdd.mockResolvedValue([]);
    renderPage();
    await screen.findByText("d1174839.0");
    fireEvent.click(screen.getByTestId("admin-row-menu-trigger-d1174839.0"));
    fireEvent.click(await screen.findByTestId("smime-relations-d1174839.0"));
    const dialog = await screen.findByTestId("smime-relations");
    expect(await within(dialog).findByText(i18n.t("admin.smime.relationsEmpty"))).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("smime-relation-select"));
    fireEvent.click(await screen.findByTestId("smime-relation-select-menu-option-2ea8c67d.0"));
    fireEvent.click(screen.getByTestId("smime-relation-add"));
    await waitFor(() => expect(smimeRelationAdd).toHaveBeenCalledWith("d1174839.0", "2ea8c67d.0"));
  });
});
