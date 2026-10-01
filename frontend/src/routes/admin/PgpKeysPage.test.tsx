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
const settings = vi.fn();
const settingsUpdate = vi.fn();
const pgpPassphraseSet = vi.fn();
const pgpPassphraseDelete = vi.fn();

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {},
  api: {
    adminCrypto: {
      status: (...a: unknown[]) => status(...a),
      pgpList: (...a: unknown[]) => pgpList(...a),
      pgpUpload: (...a: unknown[]) => pgpUpload(...a),
      pgpDelete: (...a: unknown[]) => pgpDelete(...a),
      pgpExport: (...a: unknown[]) => pgpExport(...a),
      settings: (...a: unknown[]) => settings(...a),
      settingsUpdate: (...a: unknown[]) => settingsUpdate(...a),
      pgpPassphraseSet: (...a: unknown[]) => pgpPassphraseSet(...a),
      pgpPassphraseDelete: (...a: unknown[]) => pgpPassphraseDelete(...a),
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

function settingsOut(passphraseSource: string) {
  return {
    pgp: [
      {
        name: "pgp.enabled",
        kind: "bool",
        choices: [],
        value: false,
        source: "znuny_default",
        locked: false,
        tiqora_value: null,
        znuny_setting: "PGP",
        env_var: "TIQORA_CRYPTO_PGP_ENABLED",
      },
      {
        name: "pgp.homedir",
        kind: "str",
        choices: [],
        value: "/keys/gnupg",
        source: "env",
        locked: true,
        tiqora_value: null,
        znuny_setting: "PGP::Options",
        env_var: "TIQORA_CRYPTO_PGP_GNUPGHOME",
      },
      {
        name: "pgp.method",
        kind: "choice",
        choices: ["Detached", "Inline"],
        value: "Detached",
        source: "znuny",
        locked: true,
        tiqora_value: "Inline",
        znuny_setting: "PGP::Method",
        env_var: null,
      },
    ],
    smime: [],
    pgp_passphrases: [
      { fingerprint: FP, key_id: "FB862437", uids: secretKey.uids, source: passphraseSource },
    ],
    pgp_passphrases_error: null,
  };
}

async function openKeys() {
  fireEvent.click(await screen.findByTestId("crypto-tab-keys"));
  await screen.findByText("FB862437");
}

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
    for (const fn of [
      status,
      pgpList,
      pgpUpload,
      pgpDelete,
      pgpExport,
      settings,
      settingsUpdate,
      pgpPassphraseSet,
      pgpPassphraseDelete,
    ])
      fn.mockReset();
    settings.mockResolvedValue(settingsOut("none"));
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

  it("opens on the overview with readiness checks and a master switch", async () => {
    settingsUpdate.mockResolvedValue({
      ...settingsOut("tiqora"),
      pgp: settingsOut("tiqora").pgp.map((f) => (f.name === "pgp.enabled" ? { ...f, value: true, source: "tiqora" } : f)),
    });
    renderPage();
    expect(await screen.findByTestId("crypto-headline-pgp")).toHaveTextContent(
      i18n.t("admin.cryptoPage.isOff", { name: "PGP" }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("crypto-checks-pgp")).toHaveTextContent(
        i18n.t("admin.cryptoPage.pgpSecretKeys", { count: 1 }),
      ),
    );
    expect(screen.getByTestId("crypto-checks-pgp")).toHaveTextContent(
      i18n.t("admin.cryptoPage.passphrasesMissing"),
    );
    fireEvent.click(screen.getByTestId("crypto-master-pgp"));
    await waitFor(() => expect(settingsUpdate).toHaveBeenCalledWith({ "pgp.enabled": true }));
    expect(await screen.findByTestId("crypto-headline-pgp")).toHaveTextContent(
      i18n.t("admin.cryptoPage.isOn", { name: "PGP" }),
    );
  });

  it("lists keys as cards with Znuny id, type, status and passphrase", async () => {
    renderPage();
    await openKeys();
    expect(screen.getByText("Erika Beispiel <queue@example.org>")).toBeInTheDocument();
    expect(screen.getByTestId(`pgp-status-${FP}`)).toHaveTextContent(i18n.t("admin.pgp.status.good"));
    expect(screen.getByTestId(`pgp-status-AAAA${FP.slice(4)}`)).toHaveTextContent(
      i18n.t("admin.pgp.status.expired"),
    );
    expect(screen.getByTestId(`pgp-passphrase-${FP}`)).toHaveTextContent(
      i18n.t("admin.pgp.passphraseSource.none"),
    );
  });

  it("uploads a pasted armored key", async () => {
    pgpUpload.mockResolvedValue({ fingerprints: [FP], keys: [secretKey] });
    renderPage();
    await openKeys();
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
    await openKeys();
    fireEvent.click(screen.getByTestId(`pgp-key-${FP}-menu`));
    fireEvent.click(await screen.findByTestId(`pgp-delete-secret-${FP}`));
    fireEvent.click(await screen.findByTestId("confirm-dialog-confirm"));
    await waitFor(() => expect(pgpDelete).toHaveBeenCalledWith(FP, true));
  });

  it("shows details including the full fingerprint", async () => {
    renderPage();
    await openKeys();
    fireEvent.click(screen.getByTestId(`pgp-key-${FP}-menu`));
    fireEvent.click(await screen.findByTestId(`pgp-details-${FP}`));
    expect(await screen.findByTestId("pgp-details")).toHaveTextContent(FP);
  });

  it("groups settings into sections, locks env values and saves one section", async () => {
    settingsUpdate.mockResolvedValue(settingsOut("none"));
    renderPage();
    fireEvent.click(await screen.findByTestId("crypto-tab-settings"));
    const enabled = await screen.findByTestId("crypto-setting-pgp-enabled");
    expect(screen.queryByTestId("crypto-setting-pgp-homedir")).toBeNull(); // other section
    fireEvent.click(screen.getByTestId("crypto-section-pgp-env"));
    expect(await screen.findByTestId("crypto-setting-pgp-homedir")).toBeDisabled();
    expect(screen.getByTestId("crypto-setting-pgp-homedir-source")).toHaveTextContent(
      "TIQORA_CRYPTO_PGP_GNUPGHOME",
    );
    fireEvent.click(screen.getByTestId("crypto-section-pgp-sign"));
    expect(await screen.findByTestId("crypto-setting-pgp-method-row")).toHaveTextContent(
      i18n.t("admin.cryptoSettings.shadowed"),
    );
    expect(screen.getByTestId("crypto-setting-pgp-method-Detached")).toHaveTextContent("PGP/MIME");
    fireEvent.click(screen.getByTestId("crypto-section-pgp-general"));
    expect(screen.getByTestId("crypto-settings-save-pgp")).toBeDisabled();
    fireEvent.click(await screen.findByTestId("crypto-setting-pgp-enabled"));
    expect(enabled).toBeDefined();
    fireEvent.click(screen.getByTestId("crypto-settings-save-pgp"));
    await waitFor(() => expect(settingsUpdate).toHaveBeenCalledWith({ "pgp.enabled": true }));
  });

  it("sets a passphrase for a secret key and shows wrong-passphrase errors", async () => {
    pgpPassphraseSet.mockRejectedValueOnce(new Error("wrong passphrase for this PGP key"));
    pgpPassphraseSet.mockResolvedValueOnce(settingsOut("tiqora"));
    renderPage();
    await openKeys();
    expect(screen.getByTestId(`pgp-passphrase-${FP}`)).toHaveTextContent(
      i18n.t("admin.pgp.passphraseSource.none"),
    );
    fireEvent.click(screen.getByTestId(`pgp-passphrase-set-${FP}`));
    fireEvent.change(await screen.findByTestId("pgp-passphrase-input"), {
      target: { value: "nope" },
    });
    fireEvent.click(screen.getByTestId("pgp-passphrase-submit"));
    expect(await screen.findByTestId("pgp-passphrase-error")).toHaveTextContent("wrong passphrase");
    fireEvent.change(screen.getByTestId("pgp-passphrase-input"), { target: { value: "right" } });
    fireEvent.click(screen.getByTestId("pgp-passphrase-submit"));
    await waitFor(() => expect(pgpPassphraseSet).toHaveBeenLastCalledWith(FP, "right"));
    await waitFor(() =>
      expect(screen.getByTestId(`pgp-passphrase-${FP}`)).toHaveTextContent(
        i18n.t("admin.pgp.passphraseSource.tiqora"),
      ),
    );
  });
});
