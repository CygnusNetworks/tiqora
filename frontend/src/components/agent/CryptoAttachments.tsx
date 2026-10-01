import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toBcp47 } from "@/i18n";
import { api, type AttachmentMetaOut, type AttachmentPgpKeyOut } from "@/lib/api";
import { formatBytes, formatDateOnly } from "@/lib/format";
import { cn } from "@/lib/cn";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Spinner } from "@/components/ui/Spinner";
import {
  CRYPTO_CHIP as CHIP,
  CRYPTO_ORDER as ORDER,
  formatFingerprint,
  isCryptoAttachment,
  type CryptoAtt,
} from "@/lib/cryptoAttachment";
import { ArticleBodyRenderer } from "./ArticleBodyRenderer";

function isViewableHtml(a: CryptoAtt): boolean {
  return a.crypto_kind === "pgp_html_body" && /\.html?$/i.test(a.filename ?? "");
}

const CHIP_CLASS =
  "inline-flex h-5 min-w-[2.4rem] shrink-0 items-center justify-center rounded px-1 text-[10px] font-bold tracking-wide";

function FileLink({
  ticketId,
  articleId,
  att,
}: {
  ticketId: number;
  articleId: number;
  att: CryptoAtt;
}) {
  return (
    <a
      href={api.attachmentDownloadUrl(ticketId, articleId, att.id, true)}
      download={att.filename ?? undefined}
      title={att.content_type ?? undefined}
      className="min-w-0 truncate text-muted hover:text-accent hover:underline"
      data-testid={`attachment-${att.id}`}
    >
      {att.filename || `attachment-${att.id}`}
    </a>
  );
}

function CryptoFileRow({
  ticketId,
  articleId,
  att,
  onShow,
}: {
  ticketId: number;
  articleId: number;
  att: CryptoAtt;
  onShow: (att: CryptoAtt) => void;
}) {
  const { t } = useTranslation();
  const kind = att.crypto_kind;
  return (
    <li
      className="flex items-start gap-2 text-xs"
      data-testid={`attachment-crypto-${att.id}`}
      data-kind={kind}
    >
      <span className={cn(CHIP_CLASS, "bg-surface-subtle text-muted")}>{CHIP[kind]}</span>
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-baseline gap-2">
          <span className="shrink-0 font-medium text-ink">
            {t(`ticket.cryptoAttachment.kind.${kind}`)}
          </span>
          <FileLink ticketId={ticketId} articleId={articleId} att={att} />
          <span className="shrink-0 tabular-nums text-muted">{formatBytes(att.content_size)}</span>
        </div>
        <p className="text-[11px] leading-snug text-muted">
          {t(`ticket.cryptoAttachment.hint.${kind}`)}
        </p>
      </div>
      {isViewableHtml(att) && (
        <Button
          variant="ghost"
          size="sm"
          className="shrink-0"
          onClick={() => onShow(att)}
          data-testid={`attachment-show-${att.id}`}
        >
          {t("ticket.cryptoAttachment.show")}
        </Button>
      )}
    </li>
  );
}

function KeyDetails({ keyInfo, locale }: { keyInfo: AttachmentPgpKeyOut; locale: string }) {
  const { t } = useTranslation();
  const algo = [keyInfo.algorithm, keyInfo.bits].filter(Boolean).join(" ");
  return (
    <div className="space-y-1" data-testid={`pgp-key-${keyInfo.fingerprint}`}>
      <ul className="space-y-0.5">
        {keyInfo.uids.map((uid, i) => (
          <li key={uid} className={cn("break-all", i === 0 ? "font-medium text-ink" : "text-muted")}>
            {uid}
          </li>
        ))}
      </ul>
      <p
        className="select-all whitespace-pre-wrap font-mono text-[11px] tracking-tight text-ink"
        title={t("ticket.cryptoAttachment.key.fingerprint")}
        data-testid="pgp-key-fingerprint"
      >
        {formatFingerprint(keyInfo.fingerprint)}
      </p>
      <p className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-muted">
        {algo && <span>{algo}</span>}
        <span>
          {t("ticket.cryptoAttachment.key.created")} {formatDateOnly(keyInfo.created, locale)}
        </span>
        <span>
          {t("ticket.cryptoAttachment.key.expires")}{" "}
          {keyInfo.expires
            ? formatDateOnly(keyInfo.expires, locale)
            : t("ticket.cryptoAttachment.key.never")}
        </span>
        {keyInfo.status !== "good" && (
          <Badge tone={keyInfo.status === "revoked" ? "danger" : "warn"}>
            {t(`ticket.cryptoAttachment.key.status.${keyInfo.status}`, {
              defaultValue: keyInfo.status,
            })}
          </Badge>
        )}
      </p>
    </div>
  );
}

/** A sender's public key as a card: who it belongs to, the fingerprint to
 * compare out of band, and — for agents allowed to manage keys — import. */
function PgpKeyCard({
  ticketId,
  articleId,
  att,
}: {
  ticketId: number;
  articleId: number;
  att: CryptoAtt;
}) {
  const { t, i18n } = useTranslation();
  const locale = toBcp47(i18n.language);
  const qc = useQueryClient();
  const queryKey = ["tickets", ticketId, "articles", articleId, "attachments", att.id, "pgp-key"];
  const keyQ = useQuery({
    queryKey,
    queryFn: ({ signal }) => api.getAttachmentPgpKey(ticketId, articleId, att.id, signal),
    staleTime: 60_000,
  });
  const importM = useMutation({
    mutationFn: () => api.importAttachmentPgpKey(ticketId, articleId, att.id),
    onSuccess: (data) => qc.setQueryData(queryKey, data),
  });

  const info = keyQ.data;
  const keys = info?.available ? (info.keys ?? []) : [];
  const allIn = keys.length > 0 && keys.every((k) => k.in_keyring);

  return (
    <li
      className="rounded-md border border-hairline p-2 text-xs"
      data-testid={`attachment-crypto-${att.id}`}
      data-kind={att.crypto_kind}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className={cn(CHIP_CLASS, "bg-accent-dim text-accent")}>{CHIP.pgp_public_key}</span>
        <span className="shrink-0 font-medium text-ink">
          {t("ticket.cryptoAttachment.kind.pgp_public_key")}
        </span>
        <FileLink ticketId={ticketId} articleId={articleId} att={att} />
        <span className="shrink-0 tabular-nums text-muted">{formatBytes(att.content_size)}</span>
        <span className="ml-auto flex shrink-0 items-center gap-2">
          {keys.length > 0 &&
            (allIn ? (
              <Badge tone="success" data-testid="pgp-key-in-keyring">
                {t("ticket.cryptoAttachment.key.inKeyring")}
              </Badge>
            ) : (
              <Badge tone="muted" data-testid="pgp-key-not-in-keyring">
                {t("ticket.cryptoAttachment.key.notInKeyring")}
              </Badge>
            ))}
          {info?.can_import && !allIn && keys.length > 0 && (
            <Button
              size="sm"
              variant="secondary"
              disabled={importM.isPending}
              onClick={() => importM.mutate()}
              data-testid={`pgp-key-import-${att.id}`}
            >
              {t("ticket.cryptoAttachment.key.import")}
            </Button>
          )}
        </span>
      </div>
      <div className="mt-1.5 pl-[calc(2.4rem+0.5rem)]">
        {keyQ.isLoading ? (
          <Spinner />
        ) : keys.length > 0 ? (
          <div className="space-y-2">
            {keys.map((k) => (
              <KeyDetails key={k.fingerprint} keyInfo={k} locale={locale} />
            ))}
          </div>
        ) : (
          <p className="text-[11px] text-muted" data-testid="pgp-key-unavailable">
            {t("ticket.cryptoAttachment.hint.pgp_public_key")}{" "}
            {t("ticket.cryptoAttachment.key.unavailable")}
          </p>
        )}
        {importM.isError && (
          <p className="mt-1 text-[11px] text-danger" role="alert">
            {t("ticket.cryptoAttachment.key.importFailed", {
              error: importM.error instanceof Error ? importM.error.message : "",
            })}
          </p>
        )}
      </div>
    </li>
  );
}

function HtmlAttachmentDialog({
  ticketId,
  articleId,
  att,
  onClose,
}: {
  ticketId: number;
  articleId: number;
  att: CryptoAtt;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const htmlQ = useQuery({
    queryKey: ["tickets", ticketId, "articles", articleId, "attachments", att.id, "html"],
    queryFn: ({ signal }) => api.getAttachmentHtml(ticketId, articleId, att.id, signal),
  });
  return (
    <Dialog
      open
      onClose={onClose}
      size="2xl"
      title={t("ticket.cryptoAttachment.kind.pgp_html_body")}
      description={t("ticket.cryptoAttachment.hint.pgp_html_body")}
      footer={
        <a
          className="text-xs text-muted hover:text-accent hover:underline"
          href={api.attachmentDownloadUrl(ticketId, articleId, att.id, true)}
          download={att.filename ?? undefined}
        >
          {t("ticket.download")}
        </a>
      }
    >
      <div data-testid={`attachment-html-${att.id}`}>
        {htmlQ.isLoading ? (
          <div className="flex justify-center py-4">
            <Spinner />
          </div>
        ) : htmlQ.data ? (
          <ArticleBodyRenderer body={htmlQ.data.body} isHtml={htmlQ.data.is_html} />
        ) : (
          <p className="text-xs text-muted">{t("ticket.previewFailed")}</p>
        )}
      </div>
    </Dialog>
  );
}

/** The quieter "Signature & keys" block under an article's attachments. */
export function CryptoAttachmentGroup({
  ticketId,
  articleId,
  items,
  showHeading = true,
}: {
  ticketId: number;
  articleId: number;
  items: AttachmentMetaOut[];
  showHeading?: boolean;
}) {
  const { t } = useTranslation();
  const [shown, setShown] = useState<CryptoAtt | null>(null);
  const crypto = items
    .filter(isCryptoAttachment)
    .sort((a, b) => ORDER[a.crypto_kind] - ORDER[b.crypto_kind] || a.id - b.id);
  if (crypto.length === 0) return null;
  return (
    <div data-testid="attachment-crypto-group">
      {showHeading && (
        <h5 className="mb-1 text-[11px] font-medium text-muted">
          {t("ticket.cryptoAttachment.group")}
        </h5>
      )}
      <ul className="space-y-1.5">
        {crypto.map((a) =>
          a.crypto_kind === "pgp_public_key" ? (
            <PgpKeyCard key={a.id} ticketId={ticketId} articleId={articleId} att={a} />
          ) : (
            <CryptoFileRow
              key={a.id}
              ticketId={ticketId}
              articleId={articleId}
              att={a}
              onShow={setShown}
            />
          ),
        )}
      </ul>
      {shown && (
        <HtmlAttachmentDialog
          ticketId={ticketId}
          articleId={articleId}
          att={shown}
          onClose={() => setShown(null)}
        />
      )}
    </div>
  );
}
