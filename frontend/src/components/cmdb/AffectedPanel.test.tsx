import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import i18n from "@/i18n";
import { createDemoCmdb, type DemoCmdb } from "@/demo/cmdbMock";
import type { Affected } from "@/lib/cmdbApi";
import { renderWithRouter, serveCmdb, type Seen } from "@/test/cmdbHarness";
import { AffectedPanel } from "./AffectedPanel";

let store: DemoCmdb;
const seen: Seen[] = [];
let override: Affected | null = null;

beforeEach(async () => {
  store = createDemoCmdb();
  seen.length = 0;
  override = null;
  await i18n.changeLanguage("de");
  serveCmdb(store, seen, (method, path) => (override && method === "GET" && /\/affected$/.test(path) ? override : undefined));
});
afterEach(() => vi.restoreAllMocks());

describe("AffectedPanel", () => {
  it("shows the counts and links the open tickets", async () => {
    await renderWithRouter(<AffectedPanel ciId={3} />);
    expect(await screen.findByTestId("ci-affected-count-ci")).toHaveTextContent("2");
    expect(screen.getByTestId("ci-affected-count-customers")).toHaveTextContent("1");
    expect(screen.getByTestId("ci-affected-count-tickets")).toHaveTextContent("1");
    expect(within(screen.getByTestId("ci-affected-customers")).getByText("Beispiel GmbH")).toBeInTheDocument();
    const tickets = screen.getByTestId("ci-affected-tickets");
    expect(within(tickets).getByRole("link")).toHaveAttribute("href", "/agent/tickets/1");
    expect(screen.queryByTestId("ci-affected-truncated")).toBeNull();
  });

  it("says that nothing else is affected", async () => {
    await renderWithRouter(<AffectedPanel ciId={6} />);
    expect(await screen.findByTestId("ci-affected-empty")).toHaveTextContent("nichts weiter");
  });

  it("notes a truncated list and lists services", async () => {
    override = { ci_count: 400, service_count: 1, customer_count: 0, open_ticket_count: 0, truncated: true, services: [{ id: "2", label: "VPN" }], customers: [], open_tickets: [] } as Affected;
    await renderWithRouter(<AffectedPanel ciId={3} />);
    expect(await screen.findByTestId("ci-affected-truncated")).toHaveTextContent("gekürzt");
    expect(within(screen.getByTestId("ci-affected-services")).getByText("VPN")).toBeInTheDocument();
  });
});
