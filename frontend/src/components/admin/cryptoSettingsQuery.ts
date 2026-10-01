import { useQuery } from "@tanstack/react-query";
import { api, type CryptoSettingsOut } from "@/lib/api";

export const CRYPTO_SETTINGS_KEY = ["admin", "crypto", "settings"] as const;
/** Backend self-check (`GET /admin/crypto-keys/status`). */
export const CRYPTO_STATUS_KEY = ["admin", "crypto", "status"] as const;

export type CryptoBackend = "pgp" | "smime";

/** Settings sections per backend, in display order (field names from the API). */
export const CRYPTO_SECTIONS: Record<CryptoBackend, { id: string; fields: string[] }[]> = {
  pgp: [
    { id: "general", fields: ["pgp.enabled", "pgp.trusted_network"] },
    { id: "sign", fields: ["pgp.digest", "pgp.method"] },
    { id: "env", fields: ["pgp.homedir", "pgp.gpg_bin", "pgp.options"] },
  ],
  smime: [
    { id: "general", fields: ["smime.enabled", "smime.fetch_from_customer", "smime.no_verify"] },
    { id: "store", fields: ["smime.cert_path", "smime.private_path", "smime.ca_path"] },
    { id: "env", fields: ["smime.openssl_bin"] },
  ],
};

/** Fields the backend cannot work without (empty = needs attention). */
export const REQUIRED_FIELDS: Record<CryptoBackend, string[]> = {
  pgp: ["pgp.homedir"],
  smime: ["smime.cert_path", "smime.private_path"],
};

/** Required fields that are still empty. */
export function missingRequired(data: CryptoSettingsOut | undefined, backend: CryptoBackend): string[] {
  if (!data) return [];
  return data[backend]
    .filter((f) => REQUIRED_FIELDS[backend].includes(f.name) && (f.value === "" || f.value == null))
    .map((f) => f.name);
}

/** Effective value + source of every PGP / S-MIME setting (shared query). */
export function useCryptoSettings() {
  return useQuery({
    queryKey: CRYPTO_SETTINGS_KEY,
    queryFn: ({ signal }) => api.adminCrypto.settings(signal),
  });
}

/** Backend self-check (switch, binary, key stores) — the CRYPTO_STATUS_KEY query. */
export function useCryptoStatus(backend: CryptoBackend) {
  const q = useQuery({
    queryKey: CRYPTO_STATUS_KEY,
    queryFn: ({ signal }) => api.adminCrypto.status(signal),
    staleTime: 60 * 1000,
  });
  return q.data?.find((s) => s.backend === backend) ?? null;
}
