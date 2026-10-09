import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { CustomerAuthProvider, useCustomerAuth } from "./CustomerAuthContext";
import { ApiError } from "@/lib/portalApi";

const { portalMeMock, portalLoginMock, portalLogoutMock, totpVerifyMock, submitSsoLogoutMock } =
  vi.hoisted(() => ({
    portalMeMock: vi.fn(),
    portalLoginMock: vi.fn(),
    portalLogoutMock: vi.fn(),
    totpVerifyMock: vi.fn(),
    submitSsoLogoutMock: vi.fn(),
  }));

vi.mock("@/lib/portalSecurityApi", () => ({
  customerSecurityApi: { totpVerify: totpVerifyMock },
}));

vi.mock("@/lib/ssoLogout", () => ({ submitSsoLogout: submitSsoLogoutMock }));

vi.mock("@simplewebauthn/browser", () => ({ startAuthentication: vi.fn() }));

vi.mock("@/lib/portalApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/portalApi")>();
  return {
    ...actual,
    portalApi: {
      portalMe: portalMeMock,
      portalLogin: portalLoginMock,
      portalLogout: portalLogoutMock,
    },
  };
});

const authedCustomer = {
  id: 1,
  login: "customer1",
  email: "customer1@example.com",
  first_name: "Cust",
  last_name: "Omer",
};

function wrapper({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return (
    <QueryClientProvider client={queryClient}>
      <CustomerAuthProvider>{children}</CustomerAuthProvider>
    </QueryClientProvider>
  );
}

describe("CustomerAuthContext", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    portalMeMock.mockRejectedValue(new ApiError(401, "Unauthorized", "/api/portal/auth/me"));
  });

  it("starts loading, then resolves to unauthenticated when /portal/me is 401", async () => {
    const { result } = renderHook(() => useCustomerAuth(), { wrapper });
    expect(result.current.isLoading).toBe(true);

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.isAuthenticated).toBe(false);
    expect(result.current.customer).toBeNull();
  });

  it("resolves to authenticated when /portal/me succeeds", async () => {
    portalMeMock.mockResolvedValue(authedCustomer);
    const { result } = renderHook(() => useCustomerAuth(), { wrapper });

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.isAuthenticated).toBe(true);
    expect(result.current.customer).toEqual(authedCustomer);
  });

  it("propagates non-auth errors from /portal/me instead of swallowing them", async () => {
    portalMeMock.mockRejectedValue(new Error("network down"));
    const { result } = renderHook(() => useCustomerAuth(), { wrapper });

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.isAuthenticated).toBe(false);
  });

  it("login() authenticates the customer", async () => {
    portalLoginMock.mockResolvedValue({ customer: authedCustomer });
    // A background refetch (triggered by invalidateQueries) hits /portal/me
    // again after login — keep it consistent with the just-logged-in user.
    portalMeMock.mockResolvedValue(authedCustomer);
    const { result } = renderHook(() => useCustomerAuth(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    await act(async () => {
      await result.current.login("customer1", "pw");
    });

    expect(portalLoginMock).toHaveBeenCalledWith({
      login: "customer1",
      password: "pw",
    });
    await waitFor(() => expect(result.current.isAuthenticated).toBe(true));
    expect(result.current.customer).toEqual(authedCustomer);
  });

  it("logout() clears auth state even if the API call fails", async () => {
    portalMeMock.mockResolvedValue(authedCustomer);
    portalLogoutMock.mockRejectedValue(new Error("network down"));
    const { result } = renderHook(() => useCustomerAuth(), { wrapper });
    await waitFor(() => expect(result.current.isAuthenticated).toBe(true));

    // queryClient.clear() drops the cached query, so the still-mounted hook
    // re-fetches /portal/me on the next render — match the logged-out state.
    portalMeMock.mockResolvedValue(null);
    await act(async () => {
      await result.current.logout();
    });

    expect(portalLogoutMock).toHaveBeenCalled();
    await waitFor(() => expect(result.current.isAuthenticated).toBe(false));
    expect(result.current.customer).toBeNull();
  });

  it("login() with a pending second factor keeps the customer signed out", async () => {
    portalLoginMock.mockResolvedValue({
      customer: null,
      pending_2fa: true,
      totp_enrolled: true,
      passkey_enrolled: false,
    });
    const { result } = renderHook(() => useCustomerAuth(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    let res: Awaited<ReturnType<typeof result.current.login>> | undefined;
    await act(async () => {
      res = await result.current.login("customer1", "pw");
    });

    expect(res?.pending_2fa).toBe(true);
    expect(result.current.pending2fa).toBe(true);
    expect(result.current.pendingFactors).toEqual({ totp: true, passkey: false });
    expect(result.current.isAuthenticated).toBe(false);
  });

  it("verifyTotp() completes the pending sign-in", async () => {
    portalLoginMock.mockResolvedValue({ customer: null, pending_2fa: true, totp_enrolled: true });
    totpVerifyMock.mockResolvedValue({ customer: authedCustomer });
    const { result } = renderHook(() => useCustomerAuth(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    await act(async () => {
      await result.current.login("customer1", "pw");
    });

    portalMeMock.mockResolvedValue(authedCustomer);
    await act(async () => {
      await result.current.verifyTotp("123456");
    });

    expect(totpVerifyMock).toHaveBeenCalledWith("123456");
    expect(result.current.pending2fa).toBe(false);
    expect(result.current.pendingFactors).toBeNull();
    await waitFor(() => expect(result.current.isAuthenticated).toBe(true));
  });

  it("logout() of an SSO session submits the SSO logout form instead of the plain API call", async () => {
    portalMeMock.mockResolvedValue({ ...authedCustomer, sso_logout: true });
    const { result } = renderHook(() => useCustomerAuth(), { wrapper });
    await waitFor(() => expect(result.current.isAuthenticated).toBe(true));

    await act(async () => {
      await result.current.logout();
    });

    expect(submitSsoLogoutMock).toHaveBeenCalledWith("/api/portal/auth/sso/logout");
    expect(portalLogoutMock).not.toHaveBeenCalled();
  });

  it("logout() of a password session does not use the SSO form", async () => {
    portalMeMock.mockResolvedValue({ ...authedCustomer, sso_logout: false });
    portalLogoutMock.mockResolvedValue(undefined);
    const { result } = renderHook(() => useCustomerAuth(), { wrapper });
    await waitFor(() => expect(result.current.isAuthenticated).toBe(true));
    portalMeMock.mockResolvedValue(null);

    await act(async () => {
      await result.current.logout();
    });

    expect(portalLogoutMock).toHaveBeenCalled();
    expect(submitSsoLogoutMock).not.toHaveBeenCalled();
  });

  it("refresh() re-fetches /portal/me", async () => {
    portalMeMock.mockResolvedValue(null);
    const { result } = renderHook(() => useCustomerAuth(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.isAuthenticated).toBe(false);

    portalMeMock.mockResolvedValue(authedCustomer);
    await act(async () => {
      await result.current.refresh();
    });

    await waitFor(() => expect(result.current.isAuthenticated).toBe(true));
    expect(result.current.customer).toEqual(authedCustomer);
  });

  it("throws when useCustomerAuth is used outside a CustomerAuthProvider", () => {
    function Consumer() {
      useCustomerAuth();
      return null;
    }
    expect(() => renderHook(() => Consumer())).toThrow(
      "useCustomerAuth must be used within CustomerAuthProvider",
    );
  });
});
