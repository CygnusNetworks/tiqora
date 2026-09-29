import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toBcp47 } from "@/i18n";
import { api, ApiError, type PgpKeyOut } from "@/lib/api";
import { DataTable, type DataTableColumn } from "@/components/admin/DataTable";
import { CryptoStatusBanner } from "@/components/admin/CryptoStatusBanner";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { MenuItem } from "@/components/ui/Menu";
import { PlusIcon } from "@/components/ui/icons";
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

/** Znuny AdminPGP: keyring shared with Znuny (PGP::Options --homedir). */
export function PgpKeysPage() {
  const { t, i18n } = useTranslation();
  const locale = toBcp47(i18n.language);
  const qc = useQueryClient();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const [uploadOpen, setUploadOpen] = useState(false);
  const [armor, setArmor] = useState("");
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
  const [details, setDetails] = useState<PgpKeyOut | null>(null);

  const listQ = useQuery({
    queryKey: QUERY_KEY,
    queryFn: ({ signal }) => api.adminCrypto.pgpList(signal),
  });

  const refresh = () => qc.invalidateQueries({ queryKey: QUERY_KEY });

  const uploadM = useMutation({
    mutationFn: (text: string) => api.adminCrypto.pgpUpload(text),
    onSuccess: async (res) => {
      setUploadOpen(false);
      setArmor("");
      setUploadError(null);
      setNotice({ ok: true, text: t("admin.pgp.uploaded", { count: res.fingerprints.length }) });
      await refresh();
    },
    onError: (err) => setUploadError(errText(err)),
  });

  const deleteM = useMutation({
    mutationFn: ({ key, secretOnly }: { key: PgpKeyOut; secretOnly: boolean }) =>
      api.adminCrypto.pgpDelete(key.fingerprint, secretOnly),
    onSuccess: async () => {
      setNotice({ ok: true, text: t("admin.pgp.deleted") });
      await refresh();
    },
    onError: (err) => setNotice({ ok: false, text: errText(err) }),
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

  const statusLabel = (s: string) => t(`admin.pgp.status.${s}`, { defaultValue: s });

  const columns: DataTableColumn<PgpKeyOut>[] = [
    {
      key: "type",
      header: t("admin.pgp.type"),
      render: (r) => (
        <Badge tone={r.has_secret ? "accent" : "default"}>
          {r.has_secret ? t("admin.pgp.typeSecret") : t("admin.pgp.typePublic")}
        </Badge>
      ),
    },
    { key: "key", header: t("admin.pgp.keyId"), mono: true, render: (r) => r.znuny_key_id },
    {
      key: "identity",
      header: t("admin.pgp.identity"),
      render: (r) => <span className="break-all">{r.uids.join(", ")}</span>,
    },
    {
      key: "created",
      header: t("admin.pgp.created"),
      render: (r) => formatDateOnly(r.created, locale),
    },
    {
      key: "expires",
      header: t("admin.pgp.expires"),
      render: (r) => (r.expires ? formatDateOnly(r.expires, locale) : t("admin.pgp.never")),
    },
    {
      key: "status",
      header: t("admin.table.status"),
      render: (r) => (
        <Badge tone={pgpStatusTone(r.status)} data-testid={`pgp-status-${r.fingerprint}`}>
          {statusLabel(r.status)}
        </Badge>
      ),
    },
  ];

  return (
    <div className="space-y-3 p-4" data-testid="admin-pgp-page">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h1 className="font-display text-xl font-semibold text-ink">{t("admin.pgp.title")}</h1>
          <p className="mt-1 text-sm text-muted">{t("admin.pgp.description")}</p>
        </div>
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

      <CryptoStatusBanner backend="pgp" />

      {notice ? (
        <p
          className={`text-sm ${notice.ok ? "text-green" : "text-danger"}`}
          data-testid="pgp-notice"
        >
          {notice.text}
        </p>
      ) : null}

      {listQ.isError ? (
        <p className="text-sm text-danger" data-testid="pgp-load-error">
          {t("admin.pgp.loadError")}: {errText(listQ.error)}
        </p>
      ) : (
        <DataTable
          columns={columns}
          rows={listQ.data ?? []}
          rowKey={(r) => r.fingerprint}
          isLoading={listQ.isLoading}
          emptyLabel={t("admin.pgp.empty")}
          onDelete={(r) => void onDelete(r, false)}
          extraRowActions={(r) => (
            <>
              <MenuItem testId={`pgp-details-${r.fingerprint}`} onSelect={() => setDetails(r)}>
                {t("admin.pgp.details")}
              </MenuItem>
              <MenuItem testId={`pgp-download-${r.fingerprint}`} onSelect={() => void onDownload(r)}>
                {t("admin.pgp.download")}
              </MenuItem>
              {r.has_secret ? (
                <MenuItem
                  danger
                  testId={`pgp-delete-secret-${r.fingerprint}`}
                  onSelect={() => void onDelete(r, true)}
                >
                  {t("admin.pgp.deleteSecret")}
                </MenuItem>
              ) : null}
            </>
          )}
          testId="pgp-table"
        />
      )}

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
      {confirmDialog}
    </div>
  );
}
