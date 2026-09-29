import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { PgpKeysPage } from "./PgpKeysPage";

const status = vi.fn();
const pgpList = vi.fn();
const pgpUpload = vi.fn();
const pgpDelete = vi.fn();
const pgpExport = vi.fn();

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {},
  api: {
    adminCrypto: {
      status: (...a: unknown[]) => status(...a),
      pgpList: (...a: unknown[]) => pgpList(...a),
      pgpUpload: (...a: unknown[]) => pgpUpload(...a),
      pgpDelete: (...a: unknown[]) => pgpDelete(...a),
      pgpExport: (...a: unknown[]) => pgpExport(...a),
    },
  },
}));

const FP = "F3CBC804D5B6CB0CD6C2E34A0053C56793AE002D";

const secretKey = {
  fingerprint: FP,
  key_id: "0053C56793AE002D",
  short_id: "93AE002D",
  znuny_key_id: "FB862437",
  uids: ["Erika Beispiel <queue@example.org>"],
  emails: ["queue@example.org"],
  created: "2026-09-01T00:00:00Z",
  expires: null,
  status: "good",
  has_secret: true,
  bits: 2048,
  algorithm: "RSA",
  subkey_ids: ["197CBF6EFB862437"],
};

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <PgpKeysPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("PgpKeysPage", () => {
  beforeEach(() => {
    for (const fn of [status, pgpList, pgpUpload, pgpDelete, pgpExport]) fn.mockReset();
    status.mockResolvedValue([
      {
        backend: "pgp",
        enabled: false,
        available: true,
        binary: { available: true, path: "/usr/bin/gpg", version: "gpg (GnuPG) 2.2.40", reason: "" },
        paths: { homedir: "/keys/gnupg" },
        problems: [],
      },
    ]);
    pgpList.mockResolvedValue([
      secretKey,
      {
        ...secretKey,
        fingerprint: "AAAA" + FP.slice(4),
        znuny_key_id: "11112222",
        uids: ["old@example.org"],
        status: "expired",
        has_secret: false,
      },
    ]);
  });

  it("lists keys with Znuny id, type and status badges plus the backend state", async () => {
    renderPage();
    expect(await screen.findByText("FB862437")).toBeInTheDocument();
    expect(screen.getByText("Erika Beispiel <queue@example.org>")).toBeInTheDocument();
    expect(screen.getByTestId(`pgp-status-${FP}`)).toHaveTextContent(i18n.t("admin.pgp.status.good"));
    expect(screen.getByTestId(`pgp-status-AAAA${FP.slice(4)}`)).toHaveTextContent(
      i18n.t("admin.pgp.status.expired"),
    );
    const banner = await screen.findByTestId("crypto-status-pgp");
    expect(banner).toHaveTextContent("/keys/gnupg");
    expect(screen.getByTestId("crypto-status-enabled-pgp")).toHaveTextContent(
      i18n.t("admin.crypto.disabled"),
    );
  });

  it("uploads a pasted armored key", async () => {
    pgpUpload.mockResolvedValue({ fingerprints: [FP], keys: [secretKey] });
    renderPage();
    await screen.findByText("FB862437");
    fireEvent.click(screen.getByTestId("pgp-upload-open"));
    const armor = "-----BEGIN PGP PUBLIC KEY BLOCK-----\nabc\n-----END PGP PUBLIC KEY BLOCK-----";
    fireEvent.change(screen.getByTestId("pgp-upload-armor"), { target: { value: armor } });
    fireEvent.click(screen.getByTestId("pgp-upload-submit"));
    await waitFor(() => expect(pgpUpload).toHaveBeenCalled());
    expect(pgpUpload.mock.calls[0][0]).toBe(armor);
    expect(await screen.findByTestId("pgp-notice")).toHaveTextContent(
      i18n.t("admin.pgp.uploaded", { count: 1 }),
    );
  });

  it("deletes only the secret key after confirmation", async () => {
    pgpDelete.mockResolvedValue(undefined);
    renderPage();
    await screen.findByText("FB862437");
    fireEvent.click(screen.getByTestId(`admin-row-menu-trigger-${FP}`));
    fireEvent.click(await screen.findByTestId(`pgp-delete-secret-${FP}`));
    fireEvent.click(await screen.findByTestId("confirm-dialog-confirm"));
    await waitFor(() => expect(pgpDelete).toHaveBeenCalledWith(FP, true));
  });

  it("shows details including the full fingerprint", async () => {
    renderPage();
    await screen.findByText("FB862437");
    fireEvent.click(screen.getByTestId(`admin-row-menu-trigger-${FP}`));
    fireEvent.click(await screen.findByTestId(`pgp-details-${FP}`));
    expect(await screen.findByTestId("pgp-details")).toHaveTextContent(FP);
  });
});
