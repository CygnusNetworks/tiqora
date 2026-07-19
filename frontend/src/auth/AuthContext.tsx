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
import { api, ApiError, type UserMe } from "@/lib/api";

type AuthContextValue = {
  user: UserMe | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  /** True right after login()/verifyTotp() when a TOTP code is still required. */
  pending2fa: boolean;
  login: (login: string, password: string) => Promise<void>;
  verifyTotp: (code: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [bootstrapped, setBootstrapped] = useState(false);
  const [pending2fa, setPending2fa] = useState(false);

  const meQuery = useQuery({
    queryKey: ["auth", "me"],
    queryFn: async () => {
      try {
        return await api.me();
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
      const res = await api.login({ login: loginName, password });
      if (res.pending_2fa) {
        setPending2fa(true);
        return;
      }
      setPending2fa(false);
      queryClient.setQueryData(["auth", "me"], res.user);
      await queryClient.invalidateQueries({ queryKey: ["auth", "me"] });
    },
    [queryClient],
  );

  const verifyTotp = useCallback(
    async (code: string) => {
      const res = await api.totpVerify({ code });
      setPending2fa(false);
      queryClient.setQueryData(["auth", "me"], res.user);
      await queryClient.invalidateQueries({ queryKey: ["auth", "me"] });
    },
    [queryClient],
  );

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      /* ignore network errors on logout */
    }
    setPending2fa(false);
    queryClient.setQueryData(["auth", "me"], null);
    queryClient.clear();
  }, [queryClient]);

  const refresh = useCallback(async () => {
    await meQuery.refetch();
  }, [meQuery]);

  const value = useMemo<AuthContextValue>(
    () => ({
      user: meQuery.data ?? null,
      isLoading: !bootstrapped || meQuery.isLoading,
      isAuthenticated: Boolean(meQuery.data),
      pending2fa,
      login,
      verifyTotp,
      logout,
      refresh,
    }),
    [meQuery.data, meQuery.isLoading, bootstrapped, pending2fa, login, verifyTotp, logout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
