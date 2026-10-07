import { useTranslation } from "react-i18next";
import type { CustomerCryptoKeysOut } from "@/lib/api";
import { toBcp47 } from "@/i18n";
import { CustomerCryptoKeys } from "@/components/agent/CustomerCryptoKeys";
import { statusTone } from "@/components/crypto/cryptoStatus";
import { Badge } from "@/components/ui/Badge";
import { Dialog } from "@/components/ui/Dialog";
import { KeyIcon, LockIcon } from "@/components/ui/icons";
import { formatDateOnly } from "@/lib/format";

/*
 * PGP / S/MIME on the customer page. Hardly any customer has a key, so there
 * is no card: an "add key" link next to the vCard, or — once keys exist — one
 * line under the e-mail address plus a header chip when encrypted mail works.
 * Upload and delete live in the dialog (the same one the customer-user admin uses).
 */

type KeyItem = { id: string; kind: "PGP" | "S/MIME"; status: string; expires?: string | null };

function keyItems(data: CustomerCryptoKeysOut | undefined): KeyItem[] {
  if (!data) return [];
  return [
    ...(data.pgp_enabled ? (data.pgp_keys ?? []) : []).map((k) => ({
      id: k.fingerprint,
      kind: "PGP" as const,
      status: k.status,
      expires: k.expires,
    })),
    ...(data.smime_enabled ? (data.smime_certificates ?? []) : []).map((c) => ({
      id: c.filename,
      kind: "S/MIME" as const,
      status: c.status,
      expires: c.not_after,
    })),
  ];
}

/** At least one valid key: mail to this customer can be encrypted. */
export function EncryptedReachChip({ data }: { data: CustomerCryptoKeysOut | undefined }) {
  const { t } = useTranslation();
  if (!keyItems(data).some((k) => statusTone(k.status) === "success")) return null;
  return (
    <Badge tone="success" className="gap-1" title={t("cryptoKeys.reachableHint")} data-testid="customer-keys-reachable">
      <LockIcon className="text-[12px]" />
      {t("cryptoKeys.reachable")}
    </Badge>
  );
}

/** `<dl>` entry listing the keys with status; nothing while there are none. */
export function CustomerKeysSummary({
  data,
  onOpen,
}: {
  data: CustomerCryptoKeysOut | undefined;
  onOpen: () => void;
}) {
  const { t, i18n } = useTranslation();
  const locale = toBcp47(i18n.language);
  const items = keyItems(data);
  const problems = data?.problems ?? [];
  if (!data || (items.length === 0 && problems.length === 0)) return null;

  return (
    <div data-testid="customer-keys-summary">
      <dt className="text-xs text-muted">{t("cryptoKeys.summaryLabel")}</dt>
      <dd className="flex flex-wrap items-center gap-1.5">
        {items.map((k) => {
          const tone = statusTone(k.status);
          const status = t(`cryptoKeys.status.${k.status}`, { defaultValue: k.status });
          return (
            <Badge key={k.id} tone={tone}>
              {tone === "success" && k.expires
                ? t("cryptoKeys.summaryValid", { kind: k.kind, date: formatDateOnly(k.expires, locale) })
                : t("cryptoKeys.summaryStatus", { kind: k.kind, status })}
            </Badge>
          );
        })}
        <button
          type="button"
          onClick={onOpen}
          className="text-xs text-accent hover:underline"
          data-testid="customer-keys-manage"
        >
          {data.can_edit ? t("cryptoKeys.manage") : t("cryptoKeys.view")}
        </button>
        {problems.length > 0 && <span className="basis-full text-xs text-danger">{problems.join(" · ")}</span>}
      </dd>
    </div>
  );
}

/** Quiet link beside the vCard while no key is stored (editors only). */
export function CustomerKeysAddLink({
  data,
  onOpen,
}: {
  data: CustomerCryptoKeysOut | undefined;
  onOpen: () => void;
}) {
  const { t } = useTranslation();
  if (!data || !data.can_edit || (!data.pgp_enabled && !data.smime_enabled)) return null;
  if (keyItems(data).length > 0 || (data.problems ?? []).length > 0) return null;
  return (
    <button
      type="button"
      onClick={onOpen}
      className="inline-flex items-center gap-1 text-muted hover:text-ink hover:underline"
      data-testid="customer-keys-add"
    >
      <KeyIcon className="text-[13px]" />
      {t("cryptoKeys.add")}
    </button>
  );
}

export function CustomerKeysDialog({
  login,
  open,
  onClose,
}: {
  login: string;
  open: boolean;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Dialog open={open} onClose={onClose} title={t("cryptoKeys.dialogTitle", { login })}>
      {open && <CustomerCryptoKeys login={login} />}
    </Dialog>
  );
}
