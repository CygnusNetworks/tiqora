import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { makeItsm } from "./testSupport";
import { MajorIncidentBanner } from "./MajorIncidentBanner";

const { ticketItsm } = vi.hoisted(() => ({ ticketItsm: vi.fn() }));
vi.mock("@/lib/itsmApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/itsmApi")>("@/lib/itsmApi");
  return { ...actual, itsmApi: { ...actual.itsmApi, ticketItsm } };
});

const BANNER = { ticket_id: 100, tn: "20260701000", title: "Portal down", summary: "Alle betroffen", declared_at: "2026-10-11T08:00:00Z" };

function renderBanner() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <MajorIncidentBanner ticketId={7} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("MajorIncidentBanner", () => {
  beforeEach(() => ticketItsm.mockReset());
  afterEach(cleanup);

  it("links to the active major incident", async () => {
    ticketItsm.mockResolvedValue(makeItsm({ major_banner: BANNER }));
    renderBanner();
    const link = await screen.findByTestId("major-banner-link");
    expect(link.getAttribute("href")).toBe("/agent/tickets/100");
    expect(link.textContent).toBe("20260701000");
    expect(screen.getByTestId("major-banner-summary").textContent).toBe("Alle betroffen");
  });

  it("shows nothing without a banner or while the ITSM is disabled", async () => {
    ticketItsm.mockResolvedValue(makeItsm());
    renderBanner();
    await waitFor(() => expect(ticketItsm).toHaveBeenCalled());
    expect(screen.queryByTestId("major-banner")).toBeNull();
    cleanup();
    ticketItsm.mockResolvedValue(makeItsm({ enabled: false, major_banner: BANNER }));
    renderBanner();
    await waitFor(() => expect(ticketItsm).toHaveBeenCalledTimes(2));
    expect(screen.queryByTestId("major-banner")).toBeNull();
  });
});
