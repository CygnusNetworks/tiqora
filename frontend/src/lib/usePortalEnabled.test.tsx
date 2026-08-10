import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { usePortalEnabled } from "./usePortalEnabled";

const { authMethodsMock } = vi.hoisted(() => ({
  authMethodsMock: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: { authMethods: authMethodsMock },
  };
});

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("usePortalEnabled", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("reports the portal as off when the backend says so", async () => {
    authMethodsMock.mockResolvedValue({ password: true, portal_enabled: false });
    const { result } = renderHook(() => usePortalEnabled(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.portalEnabled).toBe(false);
  });

  it("reports the portal as on when the backend says so", async () => {
    authMethodsMock.mockResolvedValue({ password: true, portal_enabled: true });
    const { result } = renderHook(() => usePortalEnabled(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.portalEnabled).toBe(true);
  });

  it("fails open: a failed discovery call must not hide a working portal", async () => {
    authMethodsMock.mockRejectedValue(new Error("network down"));
    const { result } = renderHook(() => usePortalEnabled(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.portalEnabled).toBe(true);
  });
});
