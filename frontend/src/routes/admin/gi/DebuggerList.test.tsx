import { describe, it, expect, vi, beforeEach, beforeAll } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import type { DebugEntry, DebugEntryList } from "@/lib/webservicesApi";
import { DebuggerList } from "./DebuggerList";

const debuggerList = vi.fn();
vi.mock("@/lib/webservicesApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/webservicesApi")>("@/lib/webservicesApi");
  return {
    ...actual,
    webservicesApi: {
      ...actual.webservicesApi,
      debuggerList: (...a: unknown[]) => debuggerList(...a),
      debuggerEntry: vi.fn().mockReturnValue(new Promise(() => {})),
    },
  };
});

function entry(over: Partial<DebugEntry> & { id: number }): DebugEntry {
  return {
    source: "tiqora",
    communication_id: `comm-${over.id}`,
    communication_type: "Requester",
    name: "SyncTicket",
    remote_ip: null,
    ticket_id: 105,
    created: "2026-10-11T08:00:00Z",
    max_level: "info",
    can_resend: false,
    ...over,
  };
}

function page(entries: DebugEntry[], next: string | null = null): DebugEntryList {
  return { entries, next_before: next };
}

function renderList() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <DebuggerList wsId={7} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const lastParams = () => debuggerList.mock.calls[debuggerList.mock.calls.length - 1][1];

describe("DebuggerList", () => {
  beforeAll(async () => {
    await i18n.changeLanguage("de");
  });
  beforeEach(() => {
    debuggerList.mockReset().mockResolvedValue(page([entry({ id: 1 })]));
  });

  it("maps the filters to query params", async () => {
    renderList();
    expect(await screen.findByTestId("debug-row-tiqora-1")).toBeInTheDocument();
    expect(debuggerList.mock.calls[0][0]).toBe(7);
    expect(lastParams().type).toBeUndefined();

    fireEvent.click(screen.getByTestId("debug-filter-direction-Requester"));
    await waitFor(() => expect(lastParams().type).toBe("Requester"));
    fireEvent.click(screen.getByTestId("debug-filter-direction-Provider"));
    await waitFor(() => expect(lastParams().type).toBe("Provider"));

    fireEvent.click(screen.getByTestId("debug-filter-source-znuny"));
    await waitFor(() => expect(lastParams().source).toBe("znuny"));

    fireEvent.change(screen.getByTestId("debug-filter-from"), { target: { value: "2026-10-01" } });
    await waitFor(() => expect(lastParams().from).toBe(new Date("2026-10-01T00:00:00").toISOString()));
    fireEvent.change(screen.getByTestId("debug-filter-to"), { target: { value: "2026-10-05" } });
    // "to" is inclusive in the UI, so the request ends at the start of the next day
    await waitFor(() => expect(lastParams().to).toBe(new Date("2026-10-06T00:00:00").toISOString()));

    const ip = screen.getByTestId("debug-filter-ip");
    fireEvent.change(ip, { target: { value: " 198.51.100.7 " } });
    fireEvent.blur(ip);
    await waitFor(() => expect(lastParams().remote_ip).toBe("198.51.100.7"));
    expect(lastParams().before).toBeUndefined();
  });

  it("loads more with next_before and appends without duplicates", async () => {
    debuggerList
      .mockResolvedValueOnce(page([entry({ id: 3 }), entry({ id: 2 })], "cursor-abc"))
      .mockResolvedValueOnce(page([entry({ id: 2 }), entry({ id: 1 })], null));
    renderList();
    expect(await screen.findByTestId("debug-row-tiqora-3")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("debug-load-more"));
    await waitFor(() => expect(lastParams().before).toBe("cursor-abc"));
    expect(await screen.findByTestId("debug-row-tiqora-1")).toBeInTheDocument();
    expect(screen.getAllByTestId("debug-row-tiqora-2")).toHaveLength(1);
    expect(screen.getAllByTestId(/^debug-row-/)).toHaveLength(3);
    expect(screen.queryByTestId("debug-load-more")).toBeNull();
  });

  it("colours the level badge with token classes", async () => {
    debuggerList.mockResolvedValue(
      page([entry({ id: 1, max_level: "error" }), entry({ id: 2, max_level: "notice" })]),
    );
    renderList();
    const err = await screen.findByTestId("debug-level-tiqora-1");
    expect(err).toHaveTextContent("Fehler");
    expect(err.className).toContain("text-danger");
    expect(screen.getByTestId("debug-level-tiqora-2").className).toContain("text-accent");
  });

  it("shows Znuny rows with their source and no resend button", async () => {
    debuggerList.mockResolvedValue(
      page([
        entry({ id: 5, source: "znuny", can_resend: false }),
        entry({ id: 6, source: "tiqora", can_resend: true }),
      ]),
    );
    renderList();
    const znuny = await screen.findByTestId("debug-row-znuny-5");
    expect(znuny).toHaveTextContent("Znuny");
    expect(screen.queryByTestId("debug-resend-znuny-5")).toBeNull();
    expect(screen.getByTestId("debug-resend-tiqora-6")).toHaveTextContent("Erneut senden");
  });

  it("shows an empty state and an error state", async () => {
    debuggerList.mockResolvedValue(page([]));
    renderList();
    expect(await screen.findByTestId("debug-empty")).toBeInTheDocument();
  });
});
