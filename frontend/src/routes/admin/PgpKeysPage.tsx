import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { toBcp47 } from "@/i18n";
import { api, ApiError, type PgpKeyOut } from "@/lib/api";
import { CryptoPageShell, type CryptoTab } from "@/components/admin/crypto/CryptoPageShell";
import {
  CryptoOverview,
  type NextStep,
  type OverviewCheck,
} from "@/components/admin/crypto/CryptoOverview";
import { KeyCard } from "@/components/admin/crypto/KeyCard";
import { CryptoSettingsForm } from "@/components/admin/CryptoSettingsForm";
import {
  CRYPTO_SETTINGS_KEY,
  missingRequired,
  useCryptoSettings,
  useCryptoStatus,
} from "@/components/admin/cryptoSettingsQuery";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { MenuItem } from "@/components/ui/Menu";
import { InboxIcon, KeyIcon, LockIcon, PlusIcon } from "@/components/ui/icons";
import { formatDateOnly } from "@/lib/format";
import { downloadText, readFileText } from "@/lib/cryptoFiles";

const QUERY_KEY = ["admin", "crypto", "pgp"] as const;

function errText(err: unknown): string {
  return err instanceof ApiError ? err.message : String(err);
}

function pgpStatusTone(status: string): "success" | "warn" | "danger" | "muted" {
  if (status === "good") return "success";
  if (status === "expired") return "warn";
  if (status === "revoked") return "danger";
  return "muted";
}

/** Znuny AdminPGP + PGP SysConfig: overview, keyring (shared with Znuny), settings. */
export function PgpKeysPage() {
  const { t, i18n } = useTranslation();
  const locale = toBcp47(i18n.language);
  const qc = useQueryClient();
  const router = useRouter({ warn: false }) as ReturnType<typeof useRouter> | undefined;
  const { confirm, dialog: confirmDialog } = useConfirm();
  const [tab, setTab] = useState<CryptoTab>("overview");
  const [section, setSection] = useState("general");
  const [uploadOpen, setUploadOpen] = useState(false);
  const [armor, setArmor] = useState("");
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
  const [details, setDetails] = useState<PgpKeyOut | null>(null);
  const [passphraseKey, setPassphraseKey] = useState<PgpKeyOut | null>(null);
  const [passphrase, setPassphrase] = useState("");
  const [passphraseError, setPassphraseError] = useState<string | null>(null);
  const settingsQ = useCryptoSettings();
  const passphraseSource = (key: PgpKeyOut) =>
    settingsQ.data?.pgp_passphrases.find((p) => p.fingerprint === key.fingerprint)?.source ??
    "none";

  const listQ = useQuery({
    queryKey: QUERY_KEY,
    queryFn: ({ signal }) => api.adminCrypto.pgpList(signal),
  });
  const keys = listQ.data ?? [];
  const secretKeys = keys.filter((k) => k.has_secret);

  const refresh = () => qc.invalidateQueries({ queryKey: QUERY_KEY });

  const uploadM = useMutation({
    mutationFn: (text: string) => api.adminCrypto.pgpUpload(text),
    onSuccess: async (res) => {
      setUploadOpen(false);
      setArmor("");
      setUploadError(null);
      setNotice({ ok: true, text: t("admin.pgp.uploaded", { count: res.fingerprints.length }) });
      await refresh();
      await qc.invalidateQueries({ queryKey: CRYPTO_SETTINGS_KEY });
    },
    onError: (err) => setUploadError(errText(err)),
  });

  const deleteM = useMutation({
    mutationFn: ({ key, secretOnly }: { key: PgpKeyOut; secretOnly: boolean }) =>
      api.adminCrypto.pgpDelete(key.fingerprint, secretOnly),
    onSuccess: async () => {
      setNotice({ ok: true, text: t("admin.pgp.deleted") });
      await refresh();
      await qc.invalidateQueries({ queryKey: CRYPTO_SETTINGS_KEY });
    },
    onError: (err) => setNotice({ ok: false, text: errText(err) }),
  });

  const passphraseM = useMutation({
    mutationFn: ({ key, value }: { key: PgpKeyOut; value: string | null }) =>
      value === null
        ? api.adminCrypto.pgpPassphraseDelete(key.fingerprint)
        : api.adminCrypto.pgpPassphraseSet(key.fingerprint, value),
    onSuccess: (data, { value }) => {
      qc.setQueryData(CRYPTO_SETTINGS_KEY, data);
      setPassphraseKey(null);
      setPassphrase("");
      setPassphraseError(null);
      setNotice({
        ok: true,
        text: value === null ? t("admin.pgp.passphraseRemoved") : t("admin.pgp.passphraseSaved"),
      });
    },
    onError: (err) => setPassphraseError(errText(err)),
  });

  const onDelete = async (key: PgpKeyOut, secretOnly: boolean) => {
    const ok = await confirm({
      title: secretOnly ? t("admin.pgp.deleteSecret") : t("admin.pgp.delete"),
      message: secretOnly
        ? t("admin.pgp.deleteSecretConfirm", { key: key.znuny_key_id })
        : t("admin.pgp.deleteConfirm", { key: key.znuny_key_id }),
      confirmLabel: secretOnly ? t("admin.pgp.deleteSecret") : t("admin.pgp.delete"),
      variant: "danger",
    });
    if (ok) deleteM.mutate({ key, secretOnly });
  };

  const onDownload = async (key: PgpKeyOut) => {
    try {
      const armored = await api.adminCrypto.pgpExport(key.fingerprint);
      downloadText(`${key.znuny_key_id}.asc`, armored, "application/pgp-keys");
    } catch (err) {
      setNotice({ ok: false, text: errText(err) });
    }
  };

  const openPassphrase = (key: PgpKeyOut) => {
    setPassphrase("");
    setPassphraseError(null);
    setPassphraseKey(key);
  };

  const statusLabel = (s: string) => t(`admin.pgp.status.${s}`, { defaultValue: s });

  // ------------------------------------------------------------ overview data
  const withPassphrase = secretKeys.filter((k) => passphraseSource(k) !== "none").length;
  const checks: OverviewCheck[] = listQ.isSuccess
    ? [
        {
          id: "secret-keys",
          state: secretKeys.length > 0 ? "ok" : "warn",
          title:
            secretKeys.length > 0
              ? t("admin.cryptoPage.pgpSecretKeys", { count: secretKeys.length })
              : t("admin.cryptoPage.pgpNoSecretKeys"),
          detail:
            secretKeys.length > 0
              ? secretKeys.map((k) => k.emails.join(", ") || k.znuny_key_id).join(" · ")
              : t("admin.cryptoPage.pgpNoSecretKeysHint"),
        },
        ...(secretKeys.length > 0
          ? [
              {
                id: "passphrases",
                state: (withPassphrase === secretKeys.length ? "ok" : "warn") as OverviewCheck["state"],
                title:
                  withPassphrase === secretKeys.length
                    ? t("admin.cryptoPage.passphrasesComplete")
                    : t("admin.cryptoPage.passphrasesMissing"),
                detail: t("admin.cryptoPage.passphrasesCount", {
                  done: withPassphrase,
                  total: secretKeys.length,
                }),
              },
            ]
          : []),
      ]
    : [];
  const nextSteps: NextStep[] = [];
  if (secretKeys.length === 0 || withPassphrase < secretKeys.length) {
    nextSteps.push({
      id: "keys",
      label:
        secretKeys.length === 0
          ? t("admin.cryptoPage.nextImportKey")
          : t("admin.cryptoPage.nextSetPassphrase"),
      icon: KeyIcon,
      onSelect: () => setTab("keys"),
    });
  }
  nextSteps.push({
    id: "queues",
    label: t("admin.cryptoPage.nextQueues"),
    icon: InboxIcon,
    onSelect: () => {
      if (router) void router.navigate({ to: "/admin/queues" });
    },
  });

  const status = settingsQ.data?.pgp.find((f) => f.name === "pgp.enabled");
  const backendStatus = useCryptoStatus("pgp");
  const flagged =
    missingRequired(settingsQ.data, "pgp").length > 0 || backendStatus?.available === false;

  return (
    <CryptoPageShell
      backend="pgp"
      title={t("admin.pgp.title")}
      lede={t("admin.pgp.description")}
      icon={KeyIcon}
      enabled={status ? status.value === true : null}
      ready={backendStatus ? backendStatus.available : null}
      tab={tab}
      onTab={setTab}
      keysLabel={t("admin.pgp.keysTab")}
      keyCount={listQ.isSuccess ? keys.length : null}
      settingsFlag={flagged}
    >
      {notice ? (
        <p className={`mb-3 text-sm ${notice.ok ? "text-green" : "text-danger"}`} data-testid="pgp-notice">
          {notice.text}
        </p>
      ) : null}

      {tab === "overview" ? (
        <CryptoOverview
          backend="pgp"
          name="PGP"
          checks={checks}
          nextSteps={nextSteps}
          onFix={(s) => {
            setSection(s);
            setTab("settings");
          }}
        />
      ) : null}

      {tab === "settings" ? (
        <CryptoSettingsForm backend="pgp" section={section} onSection={setSection} />
      ) : null}

      {tab === "keys" ? (
        <div className="space-y-3">
          <div className="flex flex-wrap gap-2">
            <Button
              variant="primary"
              onClick={() => {
                setUploadError(null);
                setUploadOpen(true);
              }}
              data-testid="pgp-upload-open"
            >
              <PlusIcon className="h-4 w-4" />
              {t("admin.pgp.upload")}
            </Button>
          </div>
          {listQ.isError ? (
            <p className="text-sm text-danger" data-testid="pgp-load-error">
              {t("admin.pgp.loadError")}: {errText(listQ.error)}
            </p>
          ) : listQ.isLoading ? null : keys.length === 0 ? (
            <div className="grid justify-items-center gap-2 rounded-xl border border-dashed border-hairline p-8 text-center text-sm text-muted" data-testid="pgp-empty">
              <KeyIcon className="h-6 w-6" />
              {t("admin.pgp.empty")}
            </div>
          ) : (
            <div className="grid gap-3" data-testid="pgp-keys">
              {keys.map((r) => {
                const source = passphraseSource(r);
                return (
                  <KeyCard
                    key={r.fingerprint}
                    icon={r.has_secret ? LockIcon : KeyIcon}
                    testId={`pgp-key-${r.fingerprint}`}
                    menuLabel={t("admin.table.actions")}
                    title={r.uids.join(", ") || r.znuny_key_id}
                    meta={[
                      <span key="id" className="font-mono">{r.znuny_key_id}</span>,
                      `${r.algorithm}${r.bits ? ` ${r.bits}` : ""}`,
                      t("admin.pgp.createdOn", { date: formatDateOnly(r.created, locale) }),
                      r.expires
                        ? t("admin.pgp.expiresOn", { date: formatDateOnly(r.expires, locale) })
                        : t("admin.pgp.neverExpires"),
                    ]}
                    chips={
                      <>
                        <Badge tone={r.has_secret ? "accent" : "default"}>
                          {r.has_secret ? t("admin.pgp.typeSecret") : t("admin.pgp.typePublic")}
                        </Badge>
                        <Badge tone={pgpStatusTone(r.status)} data-testid={`pgp-status-${r.fingerprint}`}>
                          {statusLabel(r.status)}
                        </Badge>
                        {r.has_secret ? (
                          <Badge
                            tone={source === "none" ? "warn" : "success"}
                            data-testid={`pgp-passphrase-${r.fingerprint}`}
                          >
                            {t("admin.pgp.passphrase")}:{" "}
                            {t(`admin.pgp.passphraseSource.${source}`, { defaultValue: source })}
                          </Badge>
                        ) : null}
                      </>
                    }
                    actions={
                      <>
                        {r.has_secret && source !== "znuny" ? (
                          <Button
                            size="sm"
                            onClick={() => openPassphrase(r)}
                            data-testid={`pgp-passphrase-set-${r.fingerprint}`}
                          >
                            {source === "none" ? t("admin.pgp.passphraseSet") : t("admin.pgp.passphraseChange")}
                          </Button>
                        ) : null}
                        <Button size="sm" onClick={() => void onDownload(r)} data-testid={`pgp-download-${r.fingerprint}`}>
                          {t("admin.pgp.download")}
                        </Button>
                      </>
                    }
                    menu={
                      <>
                        <MenuItem testId={`pgp-details-${r.fingerprint}`} onSelect={() => setDetails(r)}>
                          {t("admin.pgp.details")}
                        </MenuItem>
                        {r.has_secret && source === "tiqora" ? (
                          <MenuItem
                            testId={`pgp-passphrase-remove-${r.fingerprint}`}
                            onSelect={() => passphraseM.mutate({ key: r, value: null })}
                          >
                            {t("admin.pgp.passphraseRemove")}
                          </MenuItem>
                        ) : null}
                        {r.has_secret ? (
                          <MenuItem
                            danger
                            testId={`pgp-delete-secret-${r.fingerprint}`}
                            onSelect={() => void onDelete(r, true)}
                          >
                            {t("admin.pgp.deleteSecret")}
                          </MenuItem>
                        ) : null}
                        <MenuItem
                          danger
                          testId={`pgp-delete-${r.fingerprint}`}
                          onSelect={() => void onDelete(r, false)}
                        >
                          {t("admin.pgp.delete")}
                        </MenuItem>
                      </>
                    }
                  />
                );
              })}
            </div>
          )}
        </div>
      ) : null}

      <Dialog
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        title={t("admin.pgp.uploadTitle")}
        description={t("admin.pgp.uploadDescription")}
        size="lg"
        footer={
          <>
            <Button onClick={() => setUploadOpen(false)}>{t("common.cancel")}</Button>
            <Button
              variant="primary"
              disabled={armor.trim().length < 20 || uploadM.isPending}
              onClick={() => uploadM.mutate(armor)}
              data-testid="pgp-upload-submit"
            >
              {uploadM.isPending ? t("admin.pgp.uploading") : t("admin.pgp.upload")}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <label className="block text-sm">
            <span className="mb-1 block text-muted">{t("admin.pgp.file")}</span>
            <input
              type="file"
              accept=".asc,.gpg,.pgp,.key,.txt"
              data-testid="pgp-upload-file"
              className="block w-full text-sm text-ink file:mr-3 file:rounded-md file:border-0 file:bg-surface-subtle file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-ink hover:file:bg-hairline"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void readFileText(file).then(setArmor);
              }}
            />
          </label>
          <label className="block text-sm">
            <span className="mb-1 block text-muted">{t("admin.pgp.armor")}</span>
            <textarea
              value={armor}
              onChange={(e) => setArmor(e.target.value)}
              rows={10}
              placeholder="-----BEGIN PGP PUBLIC KEY BLOCK-----"
              data-testid="pgp-upload-armor"
              className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 font-mono text-xs text-ink"
            />
          </label>
          {uploadError ? (
            <p className="text-sm text-danger" data-testid="pgp-upload-error">
              {uploadError}
            </p>
          ) : null}
        </div>
      </Dialog>

      <Dialog
        open={details !== null}
        onClose={() => setDetails(null)}
        title={t("admin.pgp.detailsTitle", { key: details?.znuny_key_id ?? "" })}
        size="lg"
      >
        {details ? (
          <dl className="grid gap-x-4 gap-y-2 text-sm sm:grid-cols-[auto_1fr]" data-testid="pgp-details">
            <dt className="text-muted">{t("admin.pgp.fingerprint")}</dt>
            <dd className="break-all font-mono">{details.fingerprint}</dd>
            <dt className="text-muted">{t("admin.pgp.keyIdLong")}</dt>
            <dd className="font-mono">{details.key_id}</dd>
            <dt className="text-muted">{t("admin.pgp.subkeys")}</dt>
            <dd className="font-mono">{details.subkey_ids.join(", ") || "—"}</dd>
            <dt className="text-muted">{t("admin.pgp.identity")}</dt>
            <dd className="break-all">{details.uids.join(", ")}</dd>
            <dt className="text-muted">{t("admin.pgp.algorithm")}</dt>
            <dd>
              {details.algorithm}
              {details.bits ? ` ${details.bits}` : ""}
            </dd>
            <dt className="text-muted">{t("admin.pgp.created")}</dt>
            <dd>{formatDateOnly(details.created, locale)}</dd>
            <dt className="text-muted">{t("admin.pgp.expires")}</dt>
            <dd>
              {details.expires ? formatDateOnly(details.expires, locale) : t("admin.pgp.never")}
            </dd>
            <dt className="text-muted">{t("admin.table.status")}</dt>
            <dd>
              <Badge tone={pgpStatusTone(details.status)}>{statusLabel(details.status)}</Badge>
            </dd>
          </dl>
        ) : null}
      </Dialog>

      <Dialog
        open={passphraseKey !== null}
        onClose={() => setPassphraseKey(null)}
        title={t("admin.pgp.passphraseTitle", { key: passphraseKey?.znuny_key_id ?? "" })}
        description={t("admin.pgp.passphraseDescription")}
        footer={
          <>
            <Button onClick={() => setPassphraseKey(null)}>{t("common.cancel")}</Button>
            <Button
              variant="primary"
              disabled={passphrase.length === 0 || passphraseM.isPending}
              onClick={() => {
                if (passphraseKey) passphraseM.mutate({ key: passphraseKey, value: passphrase });
              }}
              data-testid="pgp-passphrase-submit"
            >
              {passphraseM.isPending ? t("admin.pgp.passphraseChecking") : t("common.save")}
            </Button>
          </>
        }
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (passphraseKey && passphrase) passphraseM.mutate({ key: passphraseKey, value: passphrase });
          }}
          className="space-y-2"
        >
          <p className="break-all text-sm text-muted">{passphraseKey?.uids.join(", ")}</p>
          <label className="block text-sm">
            <span className="mb-1 block text-muted">{t("admin.pgp.passphrase")}</span>
            <input
              type="password"
              value={passphrase}
              onChange={(e) => setPassphrase(e.target.value)}
              autoComplete="new-password"
              data-testid="pgp-passphrase-input"
              className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink"
            />
          </label>
          {passphraseError ? (
            <p className="text-sm text-danger" data-testid="pgp-passphrase-error">
              {passphraseError}
            </p>
          ) : null}
        </form>
      </Dialog>
      {confirmDialog}
    </CryptoPageShell>
  );
}
