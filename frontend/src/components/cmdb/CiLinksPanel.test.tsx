import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import i18n from "@/i18n";
import { createDemoCmdb, type DemoCmdb } from "@/demo/cmdbMock";
import { renderWithRouter, serveCmdb, type Seen } from "@/test/cmdbHarness";
import { groupLinks } from "./linksModel";
import { CiLinksPanel } from "./CiLinksPanel";

let store: DemoCmdb;
const seen: Seen[] = [];

beforeEach(async () => {
  store = createDemoCmdb();
  seen.length = 0;
  await i18n.changeLanguage("de");
  serveCmdb(store, seen);
});
afterEach(() => vi.restoreAllMocks());

describe("CiLinksPanel", () => {
  it("labels each link in the direction seen from this CI", async () => {
    await renderWithRouter(<CiLinksPanel ciId={3} canWrite />);
    // db-01 (3) is needed by web-01 (1) and app-01 (2) and itself depends on a CI the caller may not read
    expect(await screen.findByTestId("ci-link-1-type")).toHaveTextContent("wird benötigt von");
    expect(screen.getByTestId("ci-link-2-type")).toHaveTextContent("wird benötigt von");
    expect(screen.getByTestId("ci-link-4-type")).toHaveTextContent("hängt ab von");
    expect(within(screen.getByTestId("ci-link-1")).getByRole("link", { name: /web-01/ })).toHaveAttribute("href", "/agent/cmdb/1");
  });

  it("shows an unreadable CI as 'Kein Zugriff' without a link", async () => {
    await renderWithRouter(<CiLinksPanel ciId={3} canWrite />);
    const row = await screen.findByTestId("ci-link-4");
    expect(row).toHaveTextContent("Kein Zugriff");
    expect(within(row).queryByRole("link")).toBeNull();
  });

  it("groups links by the kind of the other end", async () => {
    store.links.push({ id: 50, type: "RelevantTo", source: 3, target: 0, ext: { kind: "ticket", key: "1", label: "2026100801 – Datenbank antwortet nicht", url: "/agent/tickets/1" } });
    store.links.push({ id: 51, type: "DependsOn", source: 3, target: 0, ext: { kind: "service", key: "2", label: "VPN", url: null } });
    await renderWithRouter(<CiLinksPanel ciId={3} canWrite />);
    await screen.findByTestId("ci-links");
    expect(within(screen.getByTestId("ci-links-group-ci")).getByRole("heading")).toHaveTextContent("Konfigurationselemente (3)");
    const ticketGroup = screen.getByTestId("ci-links-group-ticket");
    expect(within(ticketGroup).getByRole("link", { name: /Datenbank antwortet nicht/ })).toHaveAttribute("href", "/agent/tickets/1");
    expect(within(screen.getByTestId("ci-links-group-service")).getByText("VPN")).toBeInTheDocument();
    expect(screen.queryByTestId("ci-links-group-kb_article")).toBeNull();
  });

  it("asks before it removes a link, and removes nothing on cancel", async () => {
    await renderWithRouter(<CiLinksPanel ciId={3} canWrite />);
    fireEvent.click(await screen.findByTestId("ci-link-1-remove"));
    const dialog = await screen.findByTestId("confirm-dialog");
    expect(dialog).toHaveTextContent("wird benötigt von");
    expect(seen.some((r) => r.method === "DELETE")).toBe(false);
    fireEvent.click(screen.getByTestId("confirm-dialog-cancel"));
    await waitFor(() => expect(screen.queryByTestId("confirm-dialog")).toBeNull());
    expect(seen.some((r) => r.method === "DELETE")).toBe(false);

    fireEvent.click(screen.getByTestId("ci-link-1-remove"));
    fireEvent.click(await screen.findByTestId("confirm-dialog-confirm"));
    await waitFor(() => expect(seen.some((r) => r.method === "DELETE" && /\/links\/1$/.test(r.path))).toBe(true));
    await waitFor(() => expect(screen.queryByTestId("ci-link-1")).toBeNull());
    expect(store.links.some((l) => l.id === 1)).toBe(false);
  });

  it("does not show a failed removal again when the dialog is reopened", async () => {
    vi.restoreAllMocks();
    serveCmdb(store, seen, (m) => {
      if (m === "DELETE") throw new Error("409");
      return undefined;
    });
    await renderWithRouter(<CiLinksPanel ciId={3} canWrite />);
    fireEvent.click(await screen.findByTestId("ci-link-1-remove"));
    fireEvent.click(await screen.findByTestId("confirm-dialog-confirm"));
    expect(await screen.findByTestId("ci-link-remove-error")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("confirm-dialog-cancel"));
    await waitFor(() => expect(screen.queryByTestId("confirm-dialog")).toBeNull());
    fireEvent.click(screen.getByTestId("ci-link-1-remove"));
    expect(await screen.findByTestId("confirm-dialog")).toBeInTheDocument();
    expect(screen.queryByTestId("ci-link-remove-error")).toBeNull();
  });

  it("offers neither add nor remove to a reader", async () => {
    await renderWithRouter(<CiLinksPanel ciId={3} canWrite={false} />);
    await screen.findByTestId("ci-links");
    expect(screen.queryByTestId("ci-link-add")).toBeNull();
    expect(screen.queryByTestId("ci-link-1-remove")).toBeNull();
  });

  it("says so when a CI has no links", async () => {
    await renderWithRouter(<CiLinksPanel ciId={6} canWrite />);
    expect(await screen.findByTestId("ci-links-empty")).toHaveTextContent("Keine Verknüpfungen.");
  });
});

describe("groupLinks", () => {
  it("keeps the fixed group order and drops empty groups", () => {
    const mk = (id: number, kind: string) => ({ id, link_type: "X", link_type_label: "x", direction: "undirected", other: { kind, key: "1", label: "l", hidden: false, url: null }, created_by: null, created_at: "" }) as never;
    const groups = groupLinks([mk(1, "kb_article"), mk(2, "ci"), mk(3, "ticket"), mk(4, "ci")]);
    expect(groups.map((g) => [g.kind, g.links.length])).toEqual([["ci", 2], ["ticket", 1], ["kb_article", 1]]);
  });
});
