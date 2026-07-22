import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { usePatchTicket } from "./ticket";

const { patchTicket } = vi.hoisted(() => ({ patchTicket: vi.fn() }));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { patchTicket } };
});

describe("usePatchTicket", () => {
  beforeEach(() => {
    patchTicket.mockReset();
    patchTicket.mockResolvedValue(undefined);
  });

  it("invalidates the tickets and queues caches on success, so queue badges refresh", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );

    const { result } = renderHook(() => usePatchTicket(42), { wrapper });
    result.current.mutate({ state_id: 4 });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(patchTicket).toHaveBeenCalledWith(42, { state_id: 4 });
    expect(spy).toHaveBeenCalledWith({ queryKey: ["tickets"] });
    expect(spy).toHaveBeenCalledWith({ queryKey: ["queues"] });
  });

  it("calls the onDone callback after a successful patch", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
    const onDone = vi.fn();

    const { result } = renderHook(() => usePatchTicket(42, onDone), { wrapper });
    result.current.mutate({ owner_id: 7 });

    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
  });
});
