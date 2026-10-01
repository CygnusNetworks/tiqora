import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export const CRYPTO_SETTINGS_KEY = ["admin", "crypto", "settings"] as const;

/** Effective value + source of every PGP / S-MIME setting (shared query). */
export function useCryptoSettings() {
  return useQuery({
    queryKey: CRYPTO_SETTINGS_KEY,
    queryFn: ({ signal }) => api.adminCrypto.settings(signal),
  });
}
