import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import i18n from "@/i18n";
import { createDemoCmdb, type DemoCmdb } from "@/demo/cmdbMock";
import { renderWithRouter, serveCmdb, type Seen } from "@/test/cmdbHarness";
import { CiLinksPanel } from "./CiLinksPanel";
import { LinkDialog } from "./LinkDialog";
import { typesForKind } from "./linksModel";

let store: DemoCmdb;
const seen: Seen[] = [];

const extra = (method: string, path: string) => {
  if (method === "GET" && path.endsWith("/api/v1/tickets/search")) {
    return [{ ticket_id: 740301, tn: "2026101201", title: "Switch im Büro Bonn ausgefallen", queue: "Support", state: "open", state_type: "open" }];
  }
  if (method === "GET" && path.endsWith("/api/v1/reference/services")) return [{ id: 2, name: "VPN" }];
  return undefined;
};

async function pickKind(kind: string) {
  fireEvent.click(screen.getByTestId("ci-link-kind"));
  fireEvent.click(await screen.findByTestId(`ci-link-kind-menu-option-${kind}`));
}

async function search(testId: string, term: string) {
  const input = screen.getByTestId(testId);
  fireEvent.change(input, { target: { value: term } });
  return screen.findByTestId(`${testId}-results`);
}

beforeEach(async () => {
  store = createDemoCmdb();
  seen.length = 0;
  await i18n.changeLanguage("de");
  serveCmdb(store, seen, extra);
});
afterEach(() => vi.restoreAllMocks());

describe("LinkDialog", () => {
  it("posts a ticket link with the CI as source and refetches the links", async () => {
    await renderWithRouter(<CiLinksPanel ciId={3} canWrite />);
    await screen.findByTestId("ci-links");
    fireEvent.click(screen.getByTestId("ci-link-add"));
    await pickKind("ticket");
    await search("ci-link-target", "Switch");
    fireEvent.mouseDown(await screen.findByTestId("ci-link-target-result-740301"));
    // only types offered for ticket links
    await waitFor(() => expect(screen.getByTestId("ci-link-type")).toHaveTextContent("RelevantTo"));
    const before = seen.filter((r) => r.method === "GET" && /\/cis\/3\/links$/.test(r.path)).length;
    fireEvent.click(screen.getByTestId("ci-link-submit"));
    await waitFor(() => expect(seen.some((r) => r.method === "POST")).toBe(true));
    expect(seen.find((r) => r.method === "POST")?.body).toEqual({
      source_kind: "ci",
      source_key: "3",
      target_kind: "ticket",
      target_key: "740301",
      link_type: "RelevantTo",
    });
    await waitFor(() => expect(seen.filter((r) => r.method === "GET" && /\/cis\/3\/links$/.test(r.path)).length).toBeGreaterThan(before));
    await waitFor(() => expect(screen.queryByTestId("ci-link-dialog")).toBeNull());
    expect(await screen.findByTestId("ci-links-group-ticket")).toHaveTextContent("740301");
  });

  it("swaps source and target when the reading of a directed CI link is reversed", async () => {
    await renderWithRouter(<LinkDialog open ciId={6} onClose={() => {}} />);
    // CI picker: typeahead over the CI lookup
    await search("ci-link-target", "web");
    fireEvent.mouseDown(await screen.findByTestId("ci-link-target-result-1"));
    await waitFor(() => expect(screen.getByTestId("ci-link-type")).toHaveTextContent("DependsOn"));
    fireEvent.click(screen.getByTestId("ci-link-direction-reversed"));
    fireEvent.click(screen.getByTestId("ci-link-submit"));
    await waitFor(() => expect(seen.some((r) => r.method === "POST")).toBe(true));
    expect(seen.find((r) => r.method === "POST")?.body).toEqual({
      source_kind: "ci",
      source_key: "1",
      target_kind: "ci",
      target_key: "6",
      link_type: "DependsOn",
    });
  });

  it("posts a service link keyed by the service id", async () => {
    await renderWithRouter(<LinkDialog open ciId={6} onClose={() => {}} />);
    await pickKind("service");
    fireEvent.click(await screen.findByTestId("ci-link-target"));
    fireEvent.click(await screen.findByTestId("ci-link-target-menu-option-2"));
    await waitFor(() => expect(screen.getByTestId("ci-link-type")).toHaveTextContent("DependsOn"));
    fireEvent.click(screen.getByTestId("ci-link-submit"));
    await waitFor(() => expect(seen.some((r) => r.method === "POST")).toBe(true));
    expect(seen.find((r) => r.method === "POST")?.body).toMatchObject({ target_kind: "service", target_key: "2", link_type: "DependsOn" });
  });

  it("refuses a link from a CI to itself and keeps submit disabled until a target is picked", async () => {
    await renderWithRouter(<LinkDialog open ciId={1} onClose={() => {}} />);
    expect(screen.getByTestId("ci-link-submit")).toBeDisabled();
    await search("ci-link-target", "web");
    fireEvent.mouseDown(await screen.findByTestId("ci-link-target-result-1"));
    expect(await screen.findByTestId("ci-link-self")).toHaveTextContent("nicht mit sich selbst");
    expect(screen.getByTestId("ci-link-submit")).toBeDisabled();
    expect(seen.some((r) => r.method === "POST")).toBe(false);
  });

  it("shows an error and stays open when the server refuses the link", async () => {
    vi.restoreAllMocks();
    serveCmdb(store, seen, (m, p) => {
      if (m === "POST" && p.endsWith("/cmdb/links")) throw new Error("403");
      return extra(m, p);
    });
    await renderWithRouter(<LinkDialog open ciId={6} onClose={() => {}} />);
    await pickKind("ticket");
    await search("ci-link-target", "Switch");
    fireEvent.mouseDown(await screen.findByTestId("ci-link-target-result-740301"));
    fireEvent.click(screen.getByTestId("ci-link-submit"));
    expect(await screen.findByTestId("ci-link-error")).toBeInTheDocument();
    expect(screen.getByTestId("ci-link-dialog")).toBeInTheDocument();
  });
});

describe("typesForKind", () => {
  const types = [
    { name: "DependsOn", offer_ci_ci: true, offer_ci_service: true, offer_ticket_ci: false },
    { name: "ConnectedTo", offer_ci_ci: true, offer_ci_service: false, offer_ticket_ci: false },
    { name: "RelevantTo", offer_ci_ci: false, offer_ci_service: false, offer_ticket_ci: true },
  ] as never[];
  it("filters by the offer flags of the link type", () => {
    expect(typesForKind(types, "ci").map((x) => x.name)).toEqual(["DependsOn", "ConnectedTo"]);
    expect(typesForKind(types, "service").map((x) => x.name)).toEqual(["DependsOn"]);
    expect(typesForKind(types, "ticket").map((x) => x.name)).toEqual(["RelevantTo"]);
    expect(typesForKind(types, "kb_article")).toHaveLength(3);
  });
});
