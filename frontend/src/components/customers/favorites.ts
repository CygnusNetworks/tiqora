import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type CustomerShortlist, type CustomerUserOut } from "@/lib/api";

/** The agent's favorites, recent and frequent customers — one cache entry for all views. */
export const SHORTLIST_KEY = ["customer-directory", "shortlist"] as const;

export function useCustomerShortlist(enabled: boolean) {
  return useQuery({
    queryKey: SHORTLIST_KEY,
    queryFn: ({ signal }) => api.getCustomerShortlist(signal),
    enabled,
    staleTime: 30 * 1000,
  });
}

/** Star or unstar a customer; the shortlist cache follows at once, the server order on refetch. */
export function useFavoriteToggle() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ customer, favorite }: { customer: CustomerUserOut; favorite: boolean }) =>
      api.setCustomerFavorite(customer.login, favorite),
    onMutate: async ({ customer, favorite }) => {
      await qc.cancelQueries({ queryKey: SHORTLIST_KEY });
      const before = qc.getQueryData<CustomerShortlist>(SHORTLIST_KEY);
      if (before) {
        const rest = before.favorites.filter((f) => f.login !== customer.login);
        qc.setQueryData<CustomerShortlist>(SHORTLIST_KEY, {
          ...before,
          favorites: favorite
            ? [
                ...rest,
                {
                  login: customer.login,
                  email: customer.email,
                  customer_id: customer.customer_id,
                  company_name: customer.company_name ?? null,
                  first_name: customer.first_name,
                  last_name: customer.last_name,
                  phone: customer.phone ?? null,
                  mobile: customer.mobile ?? null,
                },
              ]
            : rest,
        });
      }
      return { before };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.before) qc.setQueryData(SHORTLIST_KEY, ctx.before);
    },
    onSettled: () => qc.invalidateQueries({ queryKey: SHORTLIST_KEY }),
  });
}
