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
import {
  startAuthentication,
  startRegistration,
  type PublicKeyCredentialCreationOptionsJSON,
  type PublicKeyCredentialRequestOptionsJSON,
} from "@simplewebauthn/browser";
import { api, ApiError, type UserMe } from "@/lib/api";

type AuthContextValue = {
  user: UserMe | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  /** True right after login()/verifyTotp() when a TOTP code is still required. */
  pending2fa: boolean;
  /**
   * Which second factors the agent mid-login actually has enrolled — the
   * login page only offers those. `null` outside a pending-2FA step (and as
   * a defensive fallback the page then treats both as available).
   */
  pendingFactors: { totp: boolean; passkey: boolean } | null;
  /**
   * True when login() returned must_enroll_2fa — the agent has a restricted
   * ENROLL session and must finish TOTP setup before a full session is issued.
   */
  mustEnroll2fa: boolean;
  login: (login: string, password: string) => Promise<void>;
  verifyTotp: (code: string) => Promise<void>;
  /**
   * Finish pending-2FA via passkey assertion (begin → browser ceremony → finish).
   * Mirrors verifyTotp: clears pending2fa and loads /me on success.
   */
  verifyPasskey: () => Promise<void>;
  /**
   * Finish forced enrollment: confirm TOTP (backend promotes ENROLL → full
   * session cookie) then load /me.
   */
  completeEnroll2fa: (code: string) => Promise<void>;
  /**
   * Finish forced enrollment via passkey registration (alternative to TOTP).
   * Backend promotes the ENROLL session to a full session on success.
   */
  completeEnrollPasskey: (name?: string | null) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [bootstrapped, setBootstrapped] = useState(false);
  const [pending2fa, setPending2fa] = useState(false);
  const [pendingFactors, setPendingFactors] = useState<{
    totp: boolean;
    passkey: boolean;
  } | null>(null);
  const [mustEnroll2fa, setMustEnroll2fa] = useState(false);

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
        setPendingFactors({
          totp: Boolean(res.totp_enrolled),
          passkey: Boolean(res.passkey_enrolled),
        });
        setMustEnroll2fa(false);
        return;
      }
      if (res.must_enroll_2fa) {
        setMustEnroll2fa(true);
        setPending2fa(false);
        return;
      }
      setPending2fa(false);
      setMustEnroll2fa(false);
      queryClient.setQueryData(["auth", "me"], res.user);
      await queryClient.invalidateQueries({ queryKey: ["auth", "me"] });
    },
    [queryClient],
  );

  const verifyTotp = useCallback(
    async (code: string) => {
      const res = await api.totpVerify({ code });
      setPending2fa(false);
      setPendingFactors(null);
      setMustEnroll2fa(false);
      queryClient.setQueryData(["auth", "me"], res.user);
      await queryClient.invalidateQueries({ queryKey: ["auth", "me"] });
    },
    [queryClient],
  );

  const verifyPasskey = useCallback(async () => {
    const options = await api.passkeyAuthenticateBegin();
    const credential = await startAuthentication({
      optionsJSON: options as unknown as PublicKeyCredentialRequestOptionsJSON,
    });
    const res = await api.passkeyAuthenticateFinish({
      credential: credential as unknown as Record<string, unknown>,
    });
    setPending2fa(false);
    setPendingFactors(null);
    setMustEnroll2fa(false);
    queryClient.setQueryData(["auth", "me"], res.user);
    await queryClient.invalidateQueries({ queryKey: ["auth", "me"] });
  }, [queryClient]);

  const completeEnroll2fa = useCallback(
    async (code: string) => {
      await api.totpConfirm({ code });
      // Backend promotes the ENROLL cookie to a full session on success.
      const me = await api.me();
      setMustEnroll2fa(false);
      setPending2fa(false);
      queryClient.setQueryData(["auth", "me"], me);
      await queryClient.invalidateQueries({ queryKey: ["auth", "me"] });
    },
    [queryClient],
  );

  const completeEnrollPasskey = useCallback(
    async (name?: string | null) => {
      const options = await api.passkeyRegisterBegin();
      const credential = await startRegistration({
        optionsJSON:
          options as unknown as PublicKeyCredentialCreationOptionsJSON,
      });
      await api.passkeyRegisterFinish({
        credential: credential as unknown as Record<string, unknown>,
        name: name?.trim() || null,
      });
      const me = await api.me();
      setMustEnroll2fa(false);
      setPending2fa(false);
      queryClient.setQueryData(["auth", "me"], me);
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
    setPendingFactors(null);
    setMustEnroll2fa(false);
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
      pendingFactors,
      mustEnroll2fa,
      login,
      verifyTotp,
      verifyPasskey,
      completeEnroll2fa,
      completeEnrollPasskey,
      logout,
      refresh,
    }),
    [
      meQuery.data,
      meQuery.isLoading,
      bootstrapped,
      pending2fa,
      pendingFactors,
      mustEnroll2fa,
      login,
      verifyTotp,
      verifyPasskey,
      completeEnroll2fa,
      completeEnrollPasskey,
      logout,
      refresh,
    ],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
