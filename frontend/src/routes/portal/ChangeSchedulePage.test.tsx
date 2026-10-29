import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import type { PortalChange } from "@/lib/portalChangeApi";
import { ChangeSchedulePage } from "./ChangeSchedulePage";

const { list } = vi.hoisted(() => ({ list: vi.fn() }));
vi.mock("@/lib/portalChangeApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/portalChangeApi")>("@/lib/portalChangeApi");
  return { ...actual, portalChangeApi: { list } };
});

const change = (tn: string, over: Partial<PortalChange> = {}): PortalChange => ({
  tn,
  title: `Wartung ${tn}`,
  change_type: "normal",
  phase: "scheduled",
  planned_start: "2026-10-30T20:00:00Z",
  planned_end: "2026-10-30T23:00:00Z",
  services: ["E-Mail", "VPN"],
  ...over,
});

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <ChangeSchedulePage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

beforeEach(async () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-29T10:00:00Z"));
  await i18n.changeLanguage("de");
  list.mockReset().mockResolvedValue({ items: [change("20261012130")], truncated: false });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("PortalChangeSchedulePage", () => {
  it("lists the windows of the month with number, title, type, phase, window and services", async () => {
    renderPage();
    const card = await screen.findByTestId("portal-change-20261012130");
    expect(card).toHaveTextContent("Wartung 20261012130");
    expect(card).toHaveTextContent("20261012130");
    expect(card).toHaveTextContent("Normaler Change");
    expect(card).toHaveTextContent("Geplant");
    expect(card).toHaveTextContent("E-Mail");
    expect(card).toHaveTextContent("VPN");
    expect(card.textContent).toMatch(/30\.10\.2026.*–.*3[01]\.10\.2026/);
    expect(screen.getByTestId("portal-changes-label")).toHaveTextContent("Oktober 2026");
  });

  it("asks for one month at a time and steps to the next", async () => {
    renderPage();
    await screen.findByTestId("portal-changes-list");
    const [start, end] = list.mock.calls[0];
    expect(start.startsWith("2026-10-01") || start.startsWith("2026-09-30")).toBe(true);
    expect(Math.round((Date.parse(end) - Date.parse(start)) / 86_400_000)).toBe(31);
    fireEvent.click(screen.getByTestId("portal-changes-next"));
    await waitFor(() => expect(screen.getByTestId("portal-changes-label")).toHaveTextContent("November 2026"));
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
    const [s2, e2] = list.mock.calls[1];
    expect(Math.round((Date.parse(e2) - Date.parse(s2)) / 86_400_000)).toBe(30);
  });

  it("shows the truncation hint when the header said so", async () => {
    list.mockResolvedValue({ items: [change("1")], truncated: true });
    renderPage();
    expect(await screen.findByTestId("portal-changes-truncated")).toBeInTheDocument();
  });

  it("shows an empty state", async () => {
    list.mockResolvedValue({ items: [], truncated: false });
    renderPage();
    expect(await screen.findByTestId("portal-changes-empty")).toBeInTheDocument();
  });

  it("says the schedule is unavailable on 404 and an error text otherwise", async () => {
    list.mockRejectedValue(new ApiError(404, "nope", "/x"));
    renderPage();
    expect(await screen.findByTestId("portal-changes-unavailable")).toBeInTheDocument();
    cleanup();
    list.mockRejectedValue(new ApiError(500, "boom", "/x"));
    renderPage();
    const err = await screen.findByTestId("portal-changes-error");
    expect(within(err.parentElement as HTMLElement).queryByTestId("portal-changes-unavailable")).toBeNull();
  });
});
