import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, type CustomerCryptoKeysOut } from "@/lib/api";
import { CryptoKeysPanel } from "@/components/crypto/CryptoKeysPanel";
import { Spinner } from "@/components/ui/Spinner";

function customerCryptoKeysQueryKey(login: string) {
  return ["customers", login, "crypto-keys"] as const;
}

/**
 * Agent view of a customer's PGP keys / S-MIME certificates (Znuny customer
 * preferences PGP / SMIME, as in AdminCustomerUser). Renders nothing while
 * both backends are off. Upload/delete only with rw in admin or users.
 */
export function CustomerCryptoKeys({
  login,
  framed = true,
}: {
  login: string;
  /** Card with heading (customer page) or bare (inside a dialog). */
  framed?: boolean;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const key = customerCryptoKeysQueryKey(login);
  const q = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => api.customerCryptoKeys.list(login, signal),
    enabled: Boolean(login),
  });

  if (q.isLoading) return framed ? null : <Spinner />;
  const data = q.data;
  if (!data || (!data.pgp_enabled && !data.smime_enabled)) {
    return framed ? null : (
      <p className="text-sm text-muted" data-testid="customer-keys-disabled">
        {t("cryptoKeys.disabled")}
      </p>
    );
  }

  const store = (next: CustomerCryptoKeysOut) => qc.setQueryData(key, next);
  const handlers = data.can_edit
    ? {
        uploadPgp: async (armor: string) => store(await api.customerCryptoKeys.uploadPgp(login, armor)),
        uploadSmime: async (cert: string) =>
          store(await api.customerCryptoKeys.uploadSmime(login, cert)),
        deletePgp: async (k: { fingerprint: string }) =>
          store(await api.customerCryptoKeys.deletePgp(login, k.fingerprint)),
        deleteSmime: async (c: { filename: string }) =>
          store(await api.customerCryptoKeys.deleteSmime(login, c.filename)),
      }
    : {};

  const body = (
    <>
      {!data.can_edit && (
        <p className="mb-3 text-xs text-muted" data-testid="customer-keys-readonly">
          {t("cryptoKeys.readOnly")}
        </p>
      )}
      {(data.problems ?? []).length > 0 && (
        <p className="mb-3 text-xs text-danger">{(data.problems ?? []).join(" · ")}</p>
      )}
      <CryptoKeysPanel data={data} handlers={handlers} testId="customer-keys" />
    </>
  );

  if (!framed) return body;
  return (
    <div className="rounded-lg border border-hairline bg-surface p-4" data-testid="customer-keys-card">
      <h2 className="mb-3 text-sm font-semibold text-ink">{t("cryptoKeys.title")}</h2>
      {body}
    </div>
  );
}
