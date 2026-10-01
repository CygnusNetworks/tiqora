import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toBcp47 } from "@/i18n";
import { api, ApiError, type SmimeCertOut } from "@/lib/api";
import { useRouter } from "@tanstack/react-router";
import { CryptoPageShell, type CryptoTab } from "@/components/admin/crypto/CryptoPageShell";
import {
  CryptoOverview,
  type NextStep,
  type OverviewCheck,
} from "@/components/admin/crypto/CryptoOverview";
import { KeyCard } from "@/components/admin/crypto/KeyCard";
import { CryptoSettingsForm } from "@/components/admin/CryptoSettingsForm";
import {
  missingRequired,
  useCryptoSettings,
  useCryptoStatus,
} from "@/components/admin/cryptoSettingsQuery";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { MenuItem } from "@/components/ui/Menu";
import { SelectField } from "@/components/ui/SelectField";
import {
  CertificateIcon,
  FolderIcon,
  InboxIcon,
  LockIcon,
  PlusIcon,
} from "@/components/ui/icons";
import { formatDateOnly } from "@/lib/format";
import { downloadText, readCertificateFile, readFileText } from "@/lib/cryptoFiles";

const QUERY_KEY = ["admin", "crypto", "smime"] as const;

const FILE_INPUT_CLASS =
  "block w-full text-sm text-ink file:mr-3 file:rounded-md file:border-0 file:bg-surface-subtle file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-ink hover:file:bg-hairline";
const TEXT_INPUT_CLASS =
  "w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink";

function errText(err: unknown): string {
  return err instanceof ApiError ? err.message : String(err);
}

function statusTone(status: string): "success" | "warn" | "danger" {
  if (status === "valid") return "success";
  if (status === "expired") return "warn";
  return "danger";
}

/** Znuny AdminSMIME + S/MIME SysConfig: overview, certificates / private keys in
 * SMIME::CertPath / SMIME::PrivatePath, settings. */
export function SmimePage() {
  const { t, i18n } = useTranslation();
  const locale = toBcp47(i18n.language);
  const qc = useQueryClient();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const router = useRouter({ warn: false }) as ReturnType<typeof useRouter> | undefined;
  const settingsQ = useCryptoSettings();
  const [tab, setTab] = useState<CryptoTab>("overview");
  const [section, setSection] = useState("general");
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
  const [certOpen, setCertOpen] = useState(false);
  const [certText, setCertText] = useState("");
  const [keyOpen, setKeyOpen] = useState(false);
  const [keyText, setKeyText] = useState("");
  const [secret, setSecret] = useState("");
  const [dialogError, setDialogError] = useState<string | null>(null);
  const [details, setDetails] = useState<SmimeCertOut | null>(null);
  const [relationsFor, setRelationsFor] = useState<SmimeCertOut | null>(null);

  const listQ = useQuery({
    queryKey: QUERY_KEY,
    queryFn: ({ signal }) => api.adminCrypto.smimeList(signal),
  });
  const certs = listQ.data ?? [];
  const refresh = () => qc.invalidateQueries({ queryKey: QUERY_KEY });

  const certM = useMutation({
    mutationFn: (text: string) => api.adminCrypto.smimeUploadCertificate(text),
    onSuccess: async (res) => {
      setCertOpen(false);
      setCertText("");
      setNotice({ ok: true, text: t("admin.smime.certAdded", { filename: res.filename }) });
      await refresh();
    },
    onError: (err) => setDialogError(errText(err)),
  });

  const keyM = useMutation({
    mutationFn: () => api.adminCrypto.smimeUploadPrivateKey(keyText, secret),
    onSuccess: async (res) => {
      setKeyOpen(false);
      setKeyText("");
      setSecret("");
      setNotice({
        ok: true,
        text: res.secret_generated
          ? t("admin.smime.keyAddedGenerated", { filename: res.certificate.filename })
          : t("admin.smime.keyAdded", { filename: res.certificate.filename }),
      });
      await refresh();
    },
    onError: (err) => setDialogError(errText(err)),
  });

  const deleteM = useMutation({
    mutationFn: ({ cert, privateOnly }: { cert: SmimeCertOut; privateOnly: boolean }) =>
      api.adminCrypto.smimeDelete(cert.filename, privateOnly),
    onSuccess: async (res) => {
      const renamed = Object.entries(res.renamed)
        .map(([a, b]) => `${a} → ${b}`)
        .join(", ");
      setNotice({
        ok: true,
        text: renamed ? `${t("admin.smime.deleted")} ${t("admin.smime.renamed", { renamed })}` : t("admin.smime.deleted"),
      });
      await refresh();
    },
    onError: (err) => setNotice({ ok: false, text: errText(err) }),
  });

  const onDelete = async (cert: SmimeCertOut, privateOnly: boolean) => {
    const ok = await confirm({
      title: privateOnly ? t("admin.smime.deletePrivate") : t("admin.smime.delete"),
      message: privateOnly
        ? t("admin.smime.deletePrivateConfirm", { filename: cert.filename })
        : t("admin.smime.deleteConfirm", { filename: cert.filename }),
      confirmLabel: privateOnly ? t("admin.smime.deletePrivate") : t("admin.smime.delete"),
      variant: "danger",
    });
    if (ok) deleteM.mutate({ cert, privateOnly });
  };

  const onDownload = async (cert: SmimeCertOut) => {
    try {
      const pem = await api.adminCrypto.smimeDownload(cert.filename);
      downloadText(`${cert.filename}.pem`, pem, "application/x-pem-file");
    } catch (err) {
      setNotice({ ok: false, text: errText(err) });
    }
  };

  const statusLabel = (s: string) => t(`admin.smime.status.${s}`, { defaultValue: s });

  // ------------------------------------------------------------ overview data
  const enabledField = settingsQ.data?.smime.find((f) => f.name === "smime.enabled");
  const backendStatus = useCryptoStatus("smime");
  const missingPaths = missingRequired(settingsQ.data, "smime");
  const flagged = missingPaths.length > 0 || backendStatus?.available === false;
  const withPrivate = certs.filter((c) => c.has_private).length;
  const checks: OverviewCheck[] = listQ.isSuccess
    ? [
        {
          id: "certificates",
          state: certs.length > 0 ? "ok" : "warn",
          title:
            certs.length > 0
              ? t("admin.cryptoPage.smimeCertificates", { count: certs.length })
              : t("admin.cryptoPage.smimeNoCertificates"),
        },
        {
          id: "private-keys",
          state: withPrivate > 0 ? "ok" : "warn",
          title:
            withPrivate > 0
              ? t("admin.cryptoPage.smimePrivateKeys", { count: withPrivate })
              : t("admin.cryptoPage.smimeNoPrivateKeys"),
          detail: withPrivate > 0 ? undefined : t("admin.cryptoPage.smimeNoPrivateKeysHint"),
        },
      ]
    : [];
  const nextSteps: NextStep[] = [];
  if (missingPaths.length > 0) {
    nextSteps.push({
      id: "store",
      label: t("admin.cryptoPage.nextStore"),
      icon: FolderIcon,
      onSelect: () => {
        setSection("store");
        setTab("settings");
      },
    });
  }
  if (missingPaths.length === 0 && (certs.length === 0 || withPrivate === 0)) {
    nextSteps.push({
      id: "certificates",
      label: t("admin.cryptoPage.nextAddCertificate"),
      icon: CertificateIcon,
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

  return (
    <CryptoPageShell
      backend="smime"
      title={t("admin.smime.title")}
      lede={t("admin.smime.description")}
      icon={CertificateIcon}
      enabled={enabledField ? enabledField.value === true : null}
      ready={backendStatus ? backendStatus.available : null}
      tab={tab}
      onTab={setTab}
      keysLabel={t("admin.smime.keysTab")}
      keyCount={listQ.isSuccess ? certs.length : null}
      settingsFlag={flagged}
    >
      {notice ? (
        <p
          className={`mb-3 text-sm ${notice.ok ? "text-green" : "text-danger"}`}
          data-testid="smime-notice"
        >
          {notice.text}
        </p>
      ) : null}

      {tab === "overview" ? (
        <CryptoOverview
          backend="smime"
          name="S/MIME"
          checks={checks}
          nextSteps={nextSteps}
          onFix={(s) => {
            setSection(s);
            setTab("settings");
          }}
        />
      ) : null}

      {tab === "settings" ? (
        <CryptoSettingsForm backend="smime" section={section} onSection={setSection} />
      ) : null}

      {tab === "keys" ? (
        <div className="space-y-3">
          <div className="flex flex-wrap gap-2">
            <Button
              variant="primary"
              onClick={() => {
                setDialogError(null);
                setCertOpen(true);
              }}
              data-testid="smime-cert-open"
            >
              <PlusIcon className="h-4 w-4" />
              {t("admin.smime.addCertificate")}
            </Button>
            <Button
              onClick={() => {
                setDialogError(null);
                setKeyOpen(true);
              }}
              data-testid="smime-key-open"
            >
              <PlusIcon className="h-4 w-4" />
              {t("admin.smime.addPrivateKey")}
            </Button>
          </div>
          {listQ.isError ? (
            <p className="text-sm text-danger" data-testid="smime-load-error">
              {t("admin.smime.loadError")}: {errText(listQ.error)}
            </p>
          ) : listQ.isLoading ? null : certs.length === 0 ? (
            <div
              className="grid justify-items-center gap-2 rounded-xl border border-dashed border-hairline p-8 text-center text-sm text-muted"
              data-testid="smime-empty"
            >
              <CertificateIcon className="h-6 w-6" />
              {t("admin.smime.empty")}
            </div>
          ) : (
            <div className="grid gap-3" data-testid="smime-certs">
              {certs.map((r) => (
                <KeyCard
                  key={r.filename}
                  icon={r.has_private ? LockIcon : CertificateIcon}
                  testId={`smime-cert-${r.filename}`}
                  menuLabel={t("admin.table.actions")}
                  title={r.subject}
                  meta={[
                    <span key="file" className="font-mono">
                      {r.filename}
                    </span>,
                    r.emails.join(", ") || "—",
                    t("admin.smime.validUntil", { date: formatDateOnly(r.not_after, locale) }),
                  ]}
                  chips={
                    <>
                      {r.is_ca ? <Badge tone="default">{t("admin.smime.ca")}</Badge> : null}
                      {r.has_private ? (
                        <Badge tone="accent" data-testid={`smime-has-private-${r.filename}`}>
                          {t("admin.smime.privateKey")}
                        </Badge>
                      ) : null}
                      <Badge tone={statusTone(r.status)} data-testid={`smime-status-${r.filename}`}>
                        {statusLabel(r.status)}
                      </Badge>
                    </>
                  }
                  actions={
                    r.valid ? (
                      <Button
                        size="sm"
                        onClick={() => void onDownload(r)}
                        data-testid={`smime-download-${r.filename}`}
                      >
                        {t("admin.smime.download")}
                      </Button>
                    ) : null
                  }
                  menu={
                    <>
                      <MenuItem testId={`smime-details-${r.filename}`} onSelect={() => setDetails(r)}>
                        {t("admin.smime.details")}
                      </MenuItem>
                      {r.has_private ? (
                        <MenuItem
                          testId={`smime-relations-${r.filename}`}
                          onSelect={() => setRelationsFor(r)}
                        >
                          {t("admin.smime.relations")}
                        </MenuItem>
                      ) : null}
                      {r.has_private ? (
                        <MenuItem
                          danger
                          testId={`smime-delete-private-${r.filename}`}
                          onSelect={() => void onDelete(r, true)}
                        >
                          {t("admin.smime.deletePrivate")}
                        </MenuItem>
                      ) : null}
                      <MenuItem
                        danger
                        testId={`smime-delete-${r.filename}`}
                        onSelect={() => void onDelete(r, false)}
                      >
                        {t("admin.smime.delete")}
                      </MenuItem>
                    </>
                  }
                />
              ))}
            </div>
          )}
        </div>
      ) : null}

      <Dialog
        open={certOpen}
        onClose={() => setCertOpen(false)}
        title={t("admin.smime.certTitle")}
        description={t("admin.smime.certDescription")}
        size="lg"
        footer={
          <>
            <Button onClick={() => setCertOpen(false)}>{t("common.cancel")}</Button>
            <Button
              variant="primary"
              disabled={certText.trim().length < 20 || certM.isPending}
              onClick={() => {
                setDialogError(null);
                certM.mutate(certText);
              }}
              data-testid="smime-cert-submit"
            >
              {t("admin.smime.addCertificate")}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <label className="block text-sm">
            <span className="mb-1 block text-muted">{t("admin.smime.certFile")}</span>
            <input
              type="file"
              accept=".pem,.crt,.cer,.der"
              data-testid="smime-cert-file"
              className={FILE_INPUT_CLASS}
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void readCertificateFile(file).then(setCertText);
              }}
            />
          </label>
          <label className="block text-sm">
            <span className="mb-1 block text-muted">{t("admin.smime.certPem")}</span>
            <textarea
              value={certText}
              onChange={(e) => setCertText(e.target.value)}
              rows={8}
              placeholder="-----BEGIN CERTIFICATE-----"
              data-testid="smime-cert-pem"
              className={`${TEXT_INPUT_CLASS} font-mono text-xs`}
            />
          </label>
          {dialogError ? (
            <p className="text-sm text-danger" data-testid="smime-dialog-error">
              {dialogError}
            </p>
          ) : null}
        </div>
      </Dialog>

      <Dialog
        open={keyOpen}
        onClose={() => setKeyOpen(false)}
        title={t("admin.smime.keyTitle")}
        description={t("admin.smime.keyDescription")}
        size="lg"
        footer={
          <>
            <Button onClick={() => setKeyOpen(false)}>{t("common.cancel")}</Button>
            <Button
              variant="primary"
              disabled={keyText.trim().length < 20 || keyM.isPending}
              onClick={() => {
                setDialogError(null);
                keyM.mutate();
              }}
              data-testid="smime-key-submit"
            >
              {t("admin.smime.addPrivateKey")}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <label className="block text-sm">
            <span className="mb-1 block text-muted">{t("admin.smime.keyFile")}</span>
            <input
              type="file"
              accept=".pem,.key"
              data-testid="smime-key-file"
              className={FILE_INPUT_CLASS}
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void readFileText(file).then(setKeyText);
              }}
            />
          </label>
          <label className="block text-sm">
            <span className="mb-1 block text-muted">{t("admin.smime.keyPem")}</span>
            <textarea
              value={keyText}
              onChange={(e) => setKeyText(e.target.value)}
              rows={8}
              placeholder="-----BEGIN ENCRYPTED PRIVATE KEY-----"
              data-testid="smime-key-pem"
              className={`${TEXT_INPUT_CLASS} font-mono text-xs`}
            />
          </label>
          <label className="block text-sm">
            <span className="mb-1 block text-muted">{t("admin.smime.secret")}</span>
            <input
              type="password"
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
              autoComplete="new-password"
              data-testid="smime-key-secret"
              className={TEXT_INPUT_CLASS}
            />
            <span className="mt-1 block text-xs text-muted">{t("admin.smime.secretHelp")}</span>
          </label>
          {dialogError ? (
            <p className="text-sm text-danger" data-testid="smime-dialog-error">
              {dialogError}
            </p>
          ) : null}
        </div>
      </Dialog>

      <Dialog
        open={details !== null}
        onClose={() => setDetails(null)}
        title={details?.filename ?? ""}
        size="lg"
      >
        {details ? (
          <dl className="grid gap-x-4 gap-y-2 text-sm sm:grid-cols-[auto_1fr]" data-testid="smime-details">
            <dt className="text-muted">{t("admin.smime.subject")}</dt>
            <dd className="break-all">{details.subject}</dd>
            <dt className="text-muted">{t("admin.smime.issuer")}</dt>
            <dd className="break-all">{details.issuer || "—"}</dd>
            <dt className="text-muted">{t("admin.smime.emails")}</dt>
            <dd className="break-all">{details.emails.join(", ") || "—"}</dd>
            <dt className="text-muted">{t("admin.smime.fingerprint")}</dt>
            <dd className="break-all font-mono">{details.fingerprint || "—"}</dd>
            <dt className="text-muted">{t("admin.smime.serial")}</dt>
            <dd className="break-all font-mono">{details.serial || "—"}</dd>
            <dt className="text-muted">{t("admin.smime.hash")}</dt>
            <dd className="font-mono">{details.hash}</dd>
            <dt className="text-muted">{t("admin.smime.notBefore")}</dt>
            <dd>{formatDateOnly(details.not_before, locale)}</dd>
            <dt className="text-muted">{t("admin.smime.expires")}</dt>
            <dd>{formatDateOnly(details.not_after, locale)}</dd>
            <dt className="text-muted">{t("admin.table.status")}</dt>
            <dd>
              <Badge tone={statusTone(details.status)}>{statusLabel(details.status)}</Badge>
            </dd>
          </dl>
        ) : null}
      </Dialog>

      {relationsFor ? (
        <RelationsDialog
          cert={relationsFor}
          certs={certs}
          onClose={() => setRelationsFor(null)}
        />
      ) : null}
      {confirmDialog}
    </CryptoPageShell>
  );
}

/** Znuny "Handle private certificate relations": CA certs attached to signatures. */
function RelationsDialog({
  cert,
  certs,
  onClose,
}: {
  cert: SmimeCertOut;
  certs: SmimeCertOut[];
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const key = ["admin", "crypto", "smime", "relations", cert.filename] as const;
  const [caFilename, setCaFilename] = useState("");
  const [error, setError] = useState<string | null>(null);

  const relQ = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => api.adminCrypto.smimeRelations(cert.filename, signal),
  });
  const related = new Set((relQ.data ?? []).map((r) => r.ca_fingerprint));
  const available = certs.filter(
    (c) => c.valid && c.fingerprint !== cert.fingerprint && !related.has(c.fingerprint),
  );

  const addM = useMutation({
    mutationFn: () => api.adminCrypto.smimeRelationAdd(cert.filename, caFilename),
    onSuccess: async () => {
      setCaFilename("");
      setError(null);
      await qc.invalidateQueries({ queryKey: key });
    },
    onError: (err) => setError(errText(err)),
  });
  const removeM = useMutation({
    mutationFn: (fp: string) => api.adminCrypto.smimeRelationDelete(cert.filename, fp),
    onSuccess: async () => {
      setError(null);
      await qc.invalidateQueries({ queryKey: key });
    },
    onError: (err) => setError(errText(err)),
  });

  return (
    <Dialog
      open
      onClose={onClose}
      title={t("admin.smime.relationsTitle", { filename: cert.filename })}
      description={t("admin.smime.relationsDescription")}
      size="xl"
    >
      <div className="space-y-4" data-testid="smime-relations">
        {relQ.isLoading ? null : (relQ.data ?? []).length === 0 ? (
          <p className="text-sm text-muted">{t("admin.smime.relationsEmpty")}</p>
        ) : (
          <ul className="divide-y divide-hairline rounded-md border border-hairline">
            {(relQ.data ?? []).map((r) => (
              <li
                key={r.ca_fingerprint}
                className="flex items-center justify-between gap-3 px-3 py-2 text-sm"
                data-testid={`smime-relation-${r.ca_fingerprint}`}
              >
                <span className="min-w-0">
                  <span className="block break-all text-ink">
                    {r.ca_subject ?? t("admin.smime.relationMissing")}
                  </span>
                  <span className="block break-all font-mono text-[11px] text-muted">
                    {r.ca_filename ? `${r.ca_filename} · ` : ""}
                    {r.ca_fingerprint}
                  </span>
                </span>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={removeM.isPending}
                  onClick={() => removeM.mutate(r.ca_fingerprint)}
                  data-testid={`smime-relation-remove-${r.ca_fingerprint}`}
                >
                  {t("admin.smime.relationRemove")}
                </Button>
              </li>
            ))}
          </ul>
        )}
        <div className="flex flex-wrap items-end gap-2">
          <label className="block min-w-64 flex-1 text-sm">
            <span className="mb-1 block text-muted">{t("admin.smime.relationSelect")}</span>
            <SelectField
              items={available.map((c) => ({ value: c.filename, label: `${c.filename} — ${c.subject}` }))}
              value={caFilename}
              onChange={(v) => setCaFilename(v)}
              testId="smime-relation-select"
            />
          </label>
          <Button
            variant="primary"
            disabled={!caFilename || addM.isPending}
            onClick={() => addM.mutate()}
            data-testid="smime-relation-add"
          >
            {t("admin.smime.relationAdd")}
          </Button>
        </div>
        {error ? (
          <p className="text-sm text-danger" data-testid="smime-relation-error">
            {error}
          </p>
        ) : null}
      </div>
    </Dialog>
  );
}
