import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { useComposerLock } from "@/lib/composerLock";
import { ComposerLockBanner } from "./ComposerLock";

const { acquireTicketLock } = vi.hoisted(() => ({ acquireTicketLock: vi.fn() }));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { acquireTicketLock } };
});

let qc: QueryClient;

beforeEach(() => {
  qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  acquireTicketLock.mockReset();
});

function Harness({ open }: { open: boolean }) {
  const lock = useComposerLock(7, "compose", open);
  return (
    <I18nextProvider i18n={i18n}>
      <ComposerLockBanner
        lockedBy={lock.lockedBy}
        onTakeOver={lock.takeOver}
        busy={lock.takingOver}
      />
      <output data-testid="locked-by">{lock.lockedBy ?? ""}</output>
    </I18nextProvider>
  );
}

function renderHarness(open = true) {
  return render(
    <QueryClientProvider client={qc}>
      <Harness open={open} />
    </QueryClientProvider>,
  );
}

describe("useComposerLock", () => {
  it("acquires once when the dialog opens and shows no banner on success", async () => {
    acquireTicketLock.mockResolvedValue({ result: "acquired" });
    const { rerender } = renderHarness();
    await waitFor(() => {
      expect(acquireTicketLock).toHaveBeenCalledWith(7, {
        action: "compose",
        takeover: false,
      });
    });
    // Re-render while open must not re-acquire.
    rerender(
      <QueryClientProvider client={qc}>
        <Harness open />
      </QueryClientProvider>,
    );
    expect(acquireTicketLock).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("composer-lock-banner")).toBeNull();
  });

  it("does not call the API while the dialog is closed", () => {
    renderHarness(false);
    expect(acquireTicketLock).not.toHaveBeenCalled();
  });

  it("shows the takeover banner on a foreign lock and takes over on click", async () => {
    acquireTicketLock
      .mockResolvedValueOnce({
        result: "locked_by_other",
        locked_by_id: 5,
        locked_by_name: "Max Mustermann",
      })
      .mockResolvedValueOnce({ result: "taken_over" });

    renderHarness();
    await waitFor(() => {
      expect(screen.getByTestId("composer-lock-banner")).toBeInTheDocument();
    });
    expect(screen.getByTestId("composer-lock-banner").textContent).toContain(
      "Max Mustermann",
    );

    fireEvent.click(screen.getByTestId("composer-lock-takeover"));
    await waitFor(() => {
      expect(acquireTicketLock).toHaveBeenLastCalledWith(7, {
        action: "compose",
        takeover: true,
      });
    });
    // Banner clears once the lock is ours.
    await waitFor(() => {
      expect(screen.queryByTestId("composer-lock-banner")).toBeNull();
    });
  });
});
