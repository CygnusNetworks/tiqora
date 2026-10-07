import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { CustomerCryptoKeysOut, PgpKeyOut, SmimeCertOut } from "@tiqora/api-client";
import { toBcp47 } from "@/i18n";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { formatDateOnly } from "@/lib/format";
import { readCertificateFile, readFileText } from "@/lib/cryptoFiles";
import { statusTone } from "./cryptoStatus";

const FILE_INPUT_CLASS =
  "block w-full text-sm text-ink file:mr-3 file:rounded-md file:border-0 file:bg-surface-subtle file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-ink hover:file:bg-hairline";

function errText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export type CryptoKeysHandlers = {
  uploadPgp?: (armor: string) => Promise<unknown>;
  uploadSmime?: (certificate: string) => Promise<unknown>;
  deletePgp?: (key: PgpKeyOut) => Promise<unknown>;
  deleteSmime?: (cert: SmimeCertOut) => Promise<unknown>;
};

/**
 * A customer's PGP keys and S/MIME certificates (Znuny customer preferences
 * PGP / SMIME): list with status + expiry, file upload, delete. Shared by the
 * agent customer page, the customer-user admin and the portal preferences.
 * Sections only render for enabled backends; handlers that are left out hide
 * the matching control.
 */
export function CryptoKeysPanel({
  data,
  handlers,
  testId = "crypto-keys",
}: {
  data: CustomerCryptoKeysOut;
  handlers: CryptoKeysHandlers;
  testId?: string;
}) {
  const { t, i18n } = useTranslation();
  const locale = toBcp47(i18n.language);
  const { confirm, dialog } = useConfirm();
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);

  const run = async (fn: () => Promise<unknown>, okText: string) => {
    setNotice(null);
    try {
      await fn();
      setNotice({ ok: true, text: okText });
    } catch (err) {
      setNotice({ ok: false, text: errText(err) });
    }
  };

  const onDeletePgp = async (key: PgpKeyOut) => {
    if (!handlers.deletePgp) return;
    const ok = await confirm({
      title: t("cryptoKeys.delete"),
      message: t("cryptoKeys.deleteConfirmPgp", { key: key.znuny_key_id }),
      confirmLabel: t("cryptoKeys.delete"),
      variant: "danger",
    });
    if (ok) await run(() => handlers.deletePgp!(key), t("cryptoKeys.deleted"));
  };

  const onDeleteSmime = async (cert: SmimeCertOut) => {
    if (!handlers.deleteSmime) return;
    const ok = await confirm({
      title: t("cryptoKeys.delete"),
      message: t("cryptoKeys.deleteConfirmSmime", { file: cert.filename }),
      confirmLabel: t("cryptoKeys.delete"),
      variant: "danger",
    });
    if (ok) await run(() => handlers.deleteSmime!(cert), t("cryptoKeys.deleted"));
  };

  return (
    <div className="space-y-4" data-testid={testId}>
      {notice && (
        <p
          className={notice.ok ? "text-sm text-green" : "text-sm text-danger"}
          data-testid={`${testId}-notice`}
          role={notice.ok ? "status" : "alert"}
        >
          {notice.text}
        </p>
      )}
      {data.pgp_enabled && (
        <section data-testid={`${testId}-pgp`}>
          <h3 className="mb-2 text-sm font-semibold text-ink">{t("cryptoKeys.pgpTitle")}</h3>
          {(data.pgp_keys ?? []).length === 0 ? (
            <p className="text-sm text-muted">{t("cryptoKeys.none")}</p>
          ) : (
            <ul className="divide-y divide-hairline rounded-md border border-hairline">
              {(data.pgp_keys ?? []).map((key) => (
                <li
                  key={key.fingerprint}
                  className="flex flex-wrap items-center gap-2 px-3 py-2 text-sm"
                  data-testid={`${testId}-pgp-row`}
                >
                  <span className="min-w-0 flex-1 truncate" title={key.fingerprint}>
                    {key.uids[0] ?? key.emails.join(", ")}
                    <span className="ml-2 font-mono text-xs text-muted">{key.znuny_key_id}</span>
                  </span>
                  <Badge tone={statusTone(key.status)}>
                    {t(`cryptoKeys.status.${key.status}`, { defaultValue: key.status })}
                  </Badge>
                  {key.expires && (
                    <span className="text-xs text-muted">
                      {t("cryptoKeys.expires", { date: formatDateOnly(key.expires, locale) })}
                    </span>
                  )}
                  {handlers.deletePgp &&
                    (key.has_secret ? (
                      <span className="text-xs text-muted">{t("cryptoKeys.adminManaged")}</span>
                    ) : (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => void onDeletePgp(key)}
                        data-testid={`${testId}-pgp-delete`}
                      >
                        {t("cryptoKeys.delete")}
                      </Button>
                    ))}
                </li>
              ))}
            </ul>
          )}
          {handlers.uploadPgp && (
            <UploadRow
              label={t("cryptoKeys.uploadPgp")}
              hint={t("cryptoKeys.pgpHint")}
              accept=".asc,.gpg,.pgp,.txt,.key"
              read={readFileText}
              testId={`${testId}-pgp-upload`}
              onUpload={(text) => run(() => handlers.uploadPgp!(text), t("cryptoKeys.uploaded"))}
            />
          )}
        </section>
      )}
      {data.smime_enabled && (
        <section data-testid={`${testId}-smime`}>
          <h3 className="mb-2 text-sm font-semibold text-ink">{t("cryptoKeys.smimeTitle")}</h3>
          {(data.smime_certificates ?? []).length === 0 ? (
            <p className="text-sm text-muted">{t("cryptoKeys.none")}</p>
          ) : (
            <ul className="divide-y divide-hairline rounded-md border border-hairline">
              {(data.smime_certificates ?? []).map((cert) => (
                <li
                  key={cert.filename}
                  className="flex flex-wrap items-center gap-2 px-3 py-2 text-sm"
                  data-testid={`${testId}-smime-row`}
                >
                  <span className="min-w-0 flex-1 truncate" title={cert.subject}>
                    {cert.emails.join(", ") || cert.subject}
                    <span className="ml-2 font-mono text-xs text-muted">{cert.filename}</span>
                  </span>
                  <Badge tone={statusTone(cert.status)}>
                    {t(`cryptoKeys.status.${cert.status}`, { defaultValue: cert.status })}
                  </Badge>
                  {cert.not_after && (
                    <span className="text-xs text-muted">
                      {t("cryptoKeys.expires", { date: formatDateOnly(cert.not_after, locale) })}
                    </span>
                  )}
                  {handlers.deleteSmime &&
                    (cert.has_private ? (
                      <span className="text-xs text-muted">{t("cryptoKeys.adminManaged")}</span>
                    ) : (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => void onDeleteSmime(cert)}
                        data-testid={`${testId}-smime-delete`}
                      >
                        {t("cryptoKeys.delete")}
                      </Button>
                    ))}
                </li>
              ))}
            </ul>
          )}
          {handlers.uploadSmime && (
            <UploadRow
              label={t("cryptoKeys.uploadSmime")}
              hint={t("cryptoKeys.smimeHint")}
              accept=".pem,.crt,.cer,.der,.p7b,.p7c,.pfx,.p12"
              read={readCertificateFile}
              testId={`${testId}-smime-upload`}
              onUpload={(text) =>
                run(() => handlers.uploadSmime!(text), t("cryptoKeys.uploaded"))
              }
            />
          )}
        </section>
      )}
      {dialog}
    </div>
  );
}

function UploadRow({
  label,
  hint,
  accept,
  read,
  testId,
  onUpload,
}: {
  label: string;
  hint: string;
  accept: string;
  read: (file: File) => Promise<string>;
  testId: string;
  onUpload: (content: string) => Promise<void>;
}) {
  const { t } = useTranslation();
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [inputKey, setInputKey] = useState(0);

  return (
    <div className="mt-2 space-y-1">
      <div className="flex flex-wrap items-center gap-2">
        <input
          key={inputKey}
          type="file"
          accept={accept}
          aria-label={label}
          data-testid={`${testId}-file`}
          className={`${FILE_INPUT_CLASS} max-w-xs`}
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
        <Button
          size="sm"
          variant="secondary"
          disabled={!file || busy}
          data-testid={`${testId}-submit`}
          onClick={() => {
            if (!file) return;
            setBusy(true);
            void read(file)
              .then(onUpload)
              .finally(() => {
                setBusy(false);
                setFile(null);
                setInputKey((k) => k + 1);
              });
          }}
        >
          {busy ? t("cryptoKeys.uploading") : label}
        </Button>
      </div>
      <p className="text-xs text-muted">{hint}</p>
    </div>
  );
}
