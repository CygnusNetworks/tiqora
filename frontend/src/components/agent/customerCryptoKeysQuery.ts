import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export function customerCryptoKeysQueryKey(login: string) {
  return ["customers", login, "crypto-keys"] as const;
}

/** A customer's keys; shared by the customer page summary and the dialog. */
export function useCustomerCryptoKeys(login: string) {
  return useQuery({
    queryKey: customerCryptoKeysQueryKey(login),
    queryFn: ({ signal }) => api.customerCryptoKeys.list(login, signal),
    enabled: Boolean(login),
  });
}
