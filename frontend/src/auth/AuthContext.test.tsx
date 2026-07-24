import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider, useAuth } from "./AuthContext";
import { ApiError } from "@/lib/api";

const {
  meMock,
  loginMock,
  totpVerifyMock,
  totpConfirmMock,
  logoutMock,
  passkeyAuthenticateBeginMock,
  passkeyAuthenticateFinishMock,
  passkeyRegisterBeginMock,
  passkeyRegisterFinishMock,
  startAuthenticationMock,
  startRegistrationMock,
  rememberLoginMethodMock,
  clearLoginMethodMock,
} = vi.hoisted(() => ({
  meMock: vi.fn(),
  loginMock: vi.fn(),
  totpVerifyMock: vi.fn(),
  totpConfirmMock: vi.fn(),
  logoutMock: vi.fn(),
  passkeyAuthenticateBeginMock: vi.fn(),
  passkeyAuthenticateFinishMock: vi.fn(),
  passkeyRegisterBeginMock: vi.fn(),
  passkeyRegisterFinishMock: vi.fn(),
  startAuthenticationMock: vi.fn(),
  startRegistrationMock: vi.fn(),
  rememberLoginMethodMock: vi.fn(),
  clearLoginMethodMock: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      me: meMock,
      login: loginMock,
      totpVerify: totpVerifyMock,
      totpConfirm: totpConfirmMock,
      logout: logoutMock,
      passkeyAuthenticateBegin: passkeyAuthenticateBeginMock,
      passkeyAuthenticateFinish: passkeyAuthenticateFinishMock,
      passkeyRegisterBegin: passkeyRegisterBeginMock,
      passkeyRegisterFinish: passkeyRegisterFinishMock,
    },
  };
});

vi.mock("@/lib/loginMethod", () => ({
  rememberLoginMethod: rememberLoginMethodMock,
  clearLoginMethod: clearLoginMethodMock,
}));

vi.mock("@simplewebauthn/browser", () => ({
  startAuthentication: startAuthenticationMock,
  startRegistration: startRegistrationMock,
}));

const authedUser = {
  id: 1,
  login: "jdoe",
  first_name: "Jane",
  last_name: "Doe",
  email: "jane@example.com",
  is_admin: false,
};

function wrapper({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>{children}</AuthProvider>
    </QueryClientProvider>
  );
}

describe("AuthContext", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    meMock.mockRejectedValue(new ApiError(401, "Unauthorized", "/api/v1/auth/me"));
  });

  it("starts loading, then resolves to unauthenticated when /me is 401", async () => {
    const { result } = renderHook(() => useAuth(), { wrapper });
    expect(result.current.isLoading).toBe(true);

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.isAuthenticated).toBe(false);
    expect(result.current.user).toBeNull();
  });

  it("resolves to authenticated when /me succeeds", async () => {
    meMock.mockResolvedValue(authedUser);
    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.isAuthenticated).toBe(true);
    expect(result.current.user).toEqual(authedUser);
  });

  it("propagates non-auth errors from /me instead of swallowing them", async () => {
    meMock.mockRejectedValue(new Error("network down"));
    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.isAuthenticated).toBe(false);
  });

  it("login() with a direct success sets the authenticated user", async () => {
    loginMock.mockResolvedValue({
      pending_2fa: false,
      must_enroll_2fa: false,
      user: authedUser,
    });
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    // login() calls invalidateQueries(["auth", "me"]), which triggers a
    // background refetch — must resolve to the authenticated user too,
    // otherwise it races the just-set state back to unauthenticated.
    meMock.mockResolvedValue(authedUser);

    await act(async () => {
      await result.current.login("jdoe", "pw");
    });

    expect(loginMock).toHaveBeenCalledWith({ login: "jdoe", password: "pw" });
    expect(rememberLoginMethodMock).toHaveBeenCalledWith("password");
    expect(result.current.isAuthenticated).toBe(true);
    expect(result.current.user).toEqual(authedUser);
    expect(result.current.pending2fa).toBe(false);
    expect(result.current.mustEnroll2fa).toBe(false);
  });

  it("login() with pending_2fa sets pending2fa and pendingFactors without authenticating", async () => {
    loginMock.mockResolvedValue({
      pending_2fa: true,
      totp_enrolled: true,
      passkey_enrolled: false,
    });
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    await act(async () => {
      await result.current.login("jdoe", "pw");
    });

    expect(result.current.pending2fa).toBe(true);
    expect(result.current.pendingFactors).toEqual({ totp: true, passkey: false });
    expect(result.current.mustEnroll2fa).toBe(false);
    expect(result.current.isAuthenticated).toBe(false);
  });

  it("login() with must_enroll_2fa sets mustEnroll2fa without authenticating", async () => {
    loginMock.mockResolvedValue({
      pending_2fa: false,
      must_enroll_2fa: true,
    });
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    await act(async () => {
      await result.current.login("jdoe", "pw");
    });

    expect(result.current.mustEnroll2fa).toBe(true);
    expect(result.current.pending2fa).toBe(false);
    expect(result.current.isAuthenticated).toBe(false);
  });

  it("verifyTotp() clears pending2fa/pendingFactors and authenticates the user", async () => {
    loginMock.mockResolvedValue({ pending_2fa: true, totp_enrolled: true, passkey_enrolled: false });
    totpVerifyMock.mockResolvedValue({ user: authedUser });
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    await act(async () => {
      await result.current.login("jdoe", "pw");
    });
    expect(result.current.pending2fa).toBe(true);

    // verifyTotp() also invalidates ["auth", "me"] — the background refetch
    // must resolve to the authenticated user, or it races the just-set
    // state back to unauthenticated.
    meMock.mockResolvedValue(authedUser);

    await act(async () => {
      await result.current.verifyTotp("123456");
    });

    expect(totpVerifyMock).toHaveBeenCalledWith({ code: "123456" });
    expect(result.current.pending2fa).toBe(false);
    expect(result.current.pendingFactors).toBeNull();
    expect(result.current.isAuthenticated).toBe(true);
    expect(result.current.user).toEqual(authedUser);
  });

  it("verifyPasskey() drives the WebAuthn ceremony and authenticates on success", async () => {
    const options = { challenge: "c" };
    const credential = { id: "cred-1" };
    passkeyAuthenticateBeginMock.mockResolvedValue(options);
    startAuthenticationMock.mockResolvedValue(credential);
    passkeyAuthenticateFinishMock.mockResolvedValue({ user: authedUser });

    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    // verifyPasskey() also invalidates ["auth", "me"] — the background
    // refetch must resolve to the authenticated user, or it races the
    // just-set state back to unauthenticated.
    meMock.mockResolvedValue(authedUser);

    await act(async () => {
      await result.current.verifyPasskey();
    });

    expect(passkeyAuthenticateBeginMock).toHaveBeenCalled();
    expect(startAuthenticationMock).toHaveBeenCalledWith({ optionsJSON: options });
    expect(passkeyAuthenticateFinishMock).toHaveBeenCalledWith({ credential });
    expect(result.current.isAuthenticated).toBe(true);
    expect(result.current.pending2fa).toBe(false);
  });

  it("completeEnroll2fa() confirms TOTP and loads /me to promote the session", async () => {
    loginMock.mockResolvedValue({ pending_2fa: false, must_enroll_2fa: true });
    totpConfirmMock.mockResolvedValue(undefined);
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    await act(async () => {
      await result.current.login("jdoe", "pw");
    });
    expect(result.current.mustEnroll2fa).toBe(true);

    meMock.mockResolvedValue(authedUser);
    await act(async () => {
      await result.current.completeEnroll2fa("654321");
    });

    expect(totpConfirmMock).toHaveBeenCalledWith({ code: "654321" });
    expect(result.current.mustEnroll2fa).toBe(false);
    expect(result.current.isAuthenticated).toBe(true);
    expect(result.current.user).toEqual(authedUser);
  });

  it("completeEnrollPasskey() registers a passkey and loads /me to promote the session", async () => {
    loginMock.mockResolvedValue({ pending_2fa: false, must_enroll_2fa: true });
    const options = { challenge: "c" };
    const credential = { id: "cred-1" };
    passkeyRegisterBeginMock.mockResolvedValue(options);
    startRegistrationMock.mockResolvedValue(credential);
    passkeyRegisterFinishMock.mockResolvedValue(undefined);

    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    await act(async () => {
      await result.current.login("jdoe", "pw");
    });

    meMock.mockResolvedValue(authedUser);
    await act(async () => {
      await result.current.completeEnrollPasskey("My Key");
    });

    expect(passkeyRegisterBeginMock).toHaveBeenCalled();
    expect(startRegistrationMock).toHaveBeenCalledWith({ optionsJSON: options });
    expect(passkeyRegisterFinishMock).toHaveBeenCalledWith({
      credential,
      name: "My Key",
    });
    expect(result.current.mustEnroll2fa).toBe(false);
    expect(result.current.isAuthenticated).toBe(true);
  });

  it("completeEnrollPasskey() normalizes a blank name to null", async () => {
    passkeyRegisterBeginMock.mockResolvedValue({});
    startRegistrationMock.mockResolvedValue({});
    passkeyRegisterFinishMock.mockResolvedValue(undefined);
    meMock.mockResolvedValue(authedUser);

    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    await act(async () => {
      await result.current.completeEnrollPasskey("   ");
    });

    expect(passkeyRegisterFinishMock).toHaveBeenCalledWith({
      credential: {},
      name: null,
    });
  });

  it("logout() clears auth state and the remembered login method even if the API call fails", async () => {
    meMock.mockResolvedValue(authedUser);
    logoutMock.mockRejectedValue(new Error("network down"));
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.isAuthenticated).toBe(true));

    await act(async () => {
      await result.current.logout();
    });

    expect(logoutMock).toHaveBeenCalled();
    expect(clearLoginMethodMock).toHaveBeenCalled();
    expect(result.current.isAuthenticated).toBe(false);
    expect(result.current.user).toBeNull();
  });

  it("refresh() re-fetches /me", async () => {
    meMock.mockResolvedValue(null);
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.isAuthenticated).toBe(false);

    meMock.mockResolvedValue(authedUser);
    await act(async () => {
      await result.current.refresh();
    });

    await waitFor(() => expect(result.current.isAuthenticated).toBe(true));
    expect(result.current.user).toEqual(authedUser);
  });

  it("throws when useAuth is used outside an AuthProvider", () => {
    function Consumer() {
      useAuth();
      return null;
    }
    expect(() => renderHook(() => Consumer())).toThrow(
      "useAuth must be used within AuthProvider",
    );
  });
});
