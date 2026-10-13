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
  type PublicKeyCredentialRequestOptionsJSON,
} from "@simplewebauthn/browser";
import { portalApi, ApiError, type CustomerMe } from "@/lib/portalApi";
import { customerSecurityApi, type CustomerLoginResponse } from "@/lib/portalSecurityApi";
import { getPortalPendingLogin } from "@/lib/identityApi";
import { submitSsoLogout } from "@/lib/ssoLogout";
import { setAppLanguage } from "@/i18n";

/** Znuny applies the customer's UserLanguage at login. */
async function applyStoredLanguage() {
  try {
    const prefs = await portalApi.portalPreferences();
    if (prefs.language) await setAppLanguage(prefs.language, { persistRemote: false });
  } catch {
    // No preferences reachable — keep the current language.
  }
}

type CustomerAuthContextValue = {
  customer: CustomerMe | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  /** True after login() when the password was accepted but a second factor is still required. */
  pending2fa: boolean;
  /** Which second factors the customer mid-login has enrolled; `null` outside a pending step. */
  pendingFactors: { totp: boolean; passkey: boolean } | null;
  login: (login: string, password: string) => Promise<CustomerLoginResponse>;
  verifyTotp: (code: string) => Promise<void>;
  /** Finish the pending step with a passkey (begin, browser ceremony, finish). */
  verifyPasskey: () => Promise<void>;
  /** Abandon a pending second-factor step and return to the password form. */
  cancelPending: () => void;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
  /** Read the login state behind the portal cookie and enter a pending second-factor step. */
  refreshPending: () => Promise<"none" | "pending_2fa" | "must_enroll_2fa">;
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
  const [pending2fa, setPending2fa] = useState(false);
  const [pendingFactors, setPendingFactors] = useState<{
    totp: boolean;
    passkey: boolean;
  } | null>(null);

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

  const finishLogin = useCallback(
    async (res: CustomerLoginResponse) => {
      setPending2fa(false);
      setPendingFactors(null);
      queryClient.setQueryData(["portal-auth", "me"], res.customer);
      await queryClient.invalidateQueries({ queryKey: ["portal-auth", "me"] });
      await applyStoredLanguage();
    },
    [queryClient],
  );

  const login = useCallback(
    async (loginName: string, password: string) => {
      const res = await portalApi.portalLogin({ login: loginName, password });
      if (res.pending_2fa) {
        // Password accepted; no usable session until the second factor is verified.
        setPending2fa(true);
        setPendingFactors({
          totp: Boolean(res.totp_enrolled),
          passkey: Boolean(res.passkey_enrolled),
        });
        return res;
      }
      await finishLogin(res);
      return res;
    },
    [finishLogin],
  );

  const verifyTotp = useCallback(
    async (code: string) => {
      await finishLogin(await customerSecurityApi.totpVerify(code));
    },
    [finishLogin],
  );

  const verifyPasskey = useCallback(async () => {
    const options = await customerSecurityApi.passkeyAuthenticateBegin();
    const credential = await startAuthentication({
      optionsJSON: options as unknown as PublicKeyCredentialRequestOptionsJSON,
    });
    await finishLogin(
      await customerSecurityApi.passkeyAuthenticateFinish(
        credential as unknown as Record<string, unknown>,
      ),
    );
  }, [finishLogin]);

  const cancelPending = useCallback(() => {
    setPending2fa(false);
    setPendingFactors(null);
  }, []);

  const refreshPending = useCallback(async () => {
    try {
      const res = await getPortalPendingLogin();
      if (res.state === "pending_2fa") {
        setPending2fa(true);
        setPendingFactors({
          totp: Boolean(res.totp_enrolled),
          passkey: Boolean(res.passkey_enrolled),
        });
      }
      return res.state;
    } catch {
      return "none" as const;
    }
  }, []);

  const ssoLogout = meQuery.data?.sso_logout === true;

  const logout = useCallback(async () => {
    setPending2fa(false);
    setPendingFactors(null);
    if (ssoLogout) {
      // The SSO logout endpoint ends the portal session and may continue at the
      // identity provider, so it is a real form POST instead of a fetch.
      queryClient.setQueryData(["portal-auth", "me"], null);
      queryClient.clear();
      submitSsoLogout("/api/portal/auth/sso/logout");
      return;
    }
    try {
      await portalApi.portalLogout();
    } catch {
      /* ignore network errors on logout */
    }
    queryClient.setQueryData(["portal-auth", "me"], null);
    queryClient.clear();
  }, [queryClient, ssoLogout]);

  const refresh = useCallback(async () => {
    await meQuery.refetch();
  }, [meQuery]);

  const value = useMemo<CustomerAuthContextValue>(
    () => ({
      customer: meQuery.data ?? null,
      isLoading: !bootstrapped || meQuery.isLoading,
      isAuthenticated: Boolean(meQuery.data),
      pending2fa,
      pendingFactors,
      login,
      verifyTotp,
      verifyPasskey,
      cancelPending,
      logout,
      refresh,
      refreshPending,
    }),
    [
      meQuery.data,
      meQuery.isLoading,
      bootstrapped,
      pending2fa,
      pendingFactors,
      login,
      verifyTotp,
      verifyPasskey,
      cancelPending,
      logout,
      refresh,
      refreshPending,
    ],
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
