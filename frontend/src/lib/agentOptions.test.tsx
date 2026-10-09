import { describe, it, expect, vi } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

const { fetchOptions, patchTicket } = vi.hoisted(() => ({ fetchOptions: vi.fn(), patchTicket: vi.fn() }));
vi.mock("./fieldOptionsApi", () => ({ fieldOptionsApi: { fetch: fetchOptions } }));
vi.mock("./api", () => ({ api: { patchTicket } }));

import { useAgentOptions } from "./agentOptions";
import { usePatchTicket } from "./ticket";

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return { qc, wrapper };
}

describe("useAgentOptions", () => {
  it("refetches a ticket's owner list after the ticket was patched (queue move)", async () => {
    fetchOptions
      .mockReset()
      .mockResolvedValueOnce({ options: { owner: [{ value: "7", label: "Old queue agent" }] } })
      .mockResolvedValue({ options: { owner: [{ value: "9", label: "New queue agent" }] } });
    patchTicket.mockReset().mockResolvedValue({});
    const { wrapper } = setup();
    const { result } = renderHook(
      () => ({ owners: useAgentOptions("owner", { ticket_id: 12 }), patch: usePatchTicket(12) }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.owners.items).toEqual([{ value: 7, label: "Old queue agent" }]));

    await act(async () => {
      result.current.patch.mutate({ queue_id: 4 } as never);
    });
    await waitFor(() => expect(result.current.owners.items).toEqual([{ value: 9, label: "New queue agent" }]));
    expect(fetchOptions).toHaveBeenCalledTimes(2);
  });

  it("flags the list as loaded only after a successful fetch", async () => {
    fetchOptions.mockReset().mockRejectedValue(new Error("boom"));
    const { wrapper } = setup();
    const { result } = renderHook(() => useAgentOptions("owner", { queue_id: 3 }), { wrapper });
    await waitFor(() => expect(fetchOptions).toHaveBeenCalled());
    expect(result.current.loaded).toBe(false);
  });
});
