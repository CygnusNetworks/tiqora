import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { portalApi, ApiError, type CustomerMe } from "@/lib/portalApi";

type CustomerAuthContextValue = {
  customer: CustomerMe | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  login: (login: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
};

const CustomerAuthContext = createContext<CustomerAuthContextValue | null>(null);

/**
 * Auth context for the customer portal (/portal). Mirrors AuthContext but is
 * backed by the portal API + portal session cookie, and is only mounted
 * inside the /portal route subtree so agent pages never touch it.
 */
export function CustomerAuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [bootstrapped, setBootstrapped] = useState(false);

  const meQuery = useQuery({
    queryKey: ["portal-auth", "me"],
    queryFn: async () => {
      try {
        return await portalApi.portalMe();
      } catch (err) {
        if (err instanceof ApiError && err.isUnauthorized) {
          return null;
        }
        throw err;
      }
    },
    retry: false,
    staleTime: 60_000,
  });

  useEffect(() => {
    if (!meQuery.isLoading) setBootstrapped(true);
  }, [meQuery.isLoading]);

  const login = useCallback(
    async (loginName: string, password: string) => {
      const res = await portalApi.portalLogin({ login: loginName, password });
      queryClient.setQueryData(["portal-auth", "me"], res.customer);
      await queryClient.invalidateQueries({ queryKey: ["portal-auth", "me"] });
    },
    [queryClient],
  );

  const logout = useCallback(async () => {
    try {
      await portalApi.portalLogout();
    } catch {
      /* ignore network errors on logout */
    }
    queryClient.setQueryData(["portal-auth", "me"], null);
    queryClient.clear();
  }, [queryClient]);

  const refresh = useCallback(async () => {
    await meQuery.refetch();
  }, [meQuery]);

  const value = useMemo<CustomerAuthContextValue>(
    () => ({
      customer: meQuery.data ?? null,
      isLoading: !bootstrapped || meQuery.isLoading,
      isAuthenticated: Boolean(meQuery.data),
      login,
      logout,
      refresh,
    }),
    [meQuery.data, meQuery.isLoading, bootstrapped, login, logout, refresh],
  );

  return (
    <CustomerAuthContext.Provider value={value}>{children}</CustomerAuthContext.Provider>
  );
}

export function useCustomerAuth(): CustomerAuthContextValue {
  const ctx = useContext(CustomerAuthContext);
  if (!ctx) {
    throw new Error("useCustomerAuth must be used within CustomerAuthProvider");
  }
  return ctx;
}
