import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, type CustomerCryptoKeysOut } from "@/lib/api";
import { CryptoKeysPanel } from "@/components/crypto/CryptoKeysPanel";
import { Spinner } from "@/components/ui/Spinner";
import { customerCryptoKeysQueryKey, useCustomerCryptoKeys } from "./customerCryptoKeysQuery";

/**
 * Agent view of a customer's PGP keys / S-MIME certificates (Znuny customer
 * preferences PGP / SMIME, as in AdminCustomerUser), meant for a dialog.
 * Upload/delete only with rw in admin or users.
 */
export function CustomerCryptoKeys({ login }: { login: string }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const key = customerCryptoKeysQueryKey(login);
  const q = useCustomerCryptoKeys(login);

  if (q.isLoading) return <Spinner />;
  const data = q.data;
  if (!data || (!data.pgp_enabled && !data.smime_enabled)) {
    return (
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

  return (
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
}
