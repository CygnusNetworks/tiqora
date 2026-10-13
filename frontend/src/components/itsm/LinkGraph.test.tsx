import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { LinkGraph } from "./LinkGraph";
import { layoutGraph, nodeHref } from "@/lib/linkGraphLayout";

const { linkGraph, navigate } = vi.hoisted(() => ({ linkGraph: vi.fn(), navigate: vi.fn() }));
vi.mock("@/lib/itsmApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/itsmApi")>("@/lib/itsmApi");
  return { ...actual, itsmApi: { ...actual.itsmApi, linkGraph } };
});
vi.mock("@tanstack/react-router", () => ({ useNavigate: () => navigate }));

const node = (o: Record<string, unknown>) => ({
  depth: 0, number: "1", title: "T", state: "open", state_type: "open",
  class_name: null, incident_state: null, incident_functionality: null, ...o,
});
const GRAPH = {
  root: "ticket:7",
  truncated: false,
  nodes: [
    node({ key: "ticket:7", kind: "ticket", number: "2026070100007", title: "Root" }),
    node({ key: "ticket:9", kind: "ticket", depth: 1, number: "2026070100009", title: "Other", state: "closed successful" }),
    node({ key: "ci:znuny:45", kind: "ci", depth: 1, number: "C-45", title: "srv-01", state: null, class_name: "Computer", incident_state: "Störung", incident_functionality: "incident" }),
  ],
  edges: [
    { source: "ticket:7", target: "ticket:9", link_type: "ParentChild", kind: "ticket_ticket" },
    { source: "ticket:7", target: "ci:znuny:45", link_type: "RelevantTo", kind: "ticket_ci" },
  ],
};

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <LinkGraph ticketId={7} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("LinkGraph", () => {
  beforeEach(() => {
    linkGraph.mockReset();
    navigate.mockReset();
  });

  it("renders nodes and edges and navigates on click", async () => {
    linkGraph.mockResolvedValue(GRAPH);
    wrap();
    expect(await screen.findByTestId("link-graph-node-ticket:9")).toBeTruthy();
    expect(screen.getAllByTestId("link-graph-edge")).toHaveLength(2);
    expect(screen.getByTestId("link-graph-color-ci:znuny:45").getAttribute("fill")).toBe("var(--color-danger)");
    expect(screen.queryByTestId("link-graph-truncated")).toBeNull();
    fireEvent.click(screen.getByTestId("link-graph-node-ticket:9"));
    expect(navigate).toHaveBeenCalledWith({ to: "/agent/tickets/9" });
    fireEvent.click(screen.getByTestId("link-graph-node-ci:znuny:45"));
    expect(navigate).toHaveBeenLastCalledWith({ to: "/agent/itsm/cis/znuny/45" });
  });

  it("says that further links are hidden when truncated", async () => {
    linkGraph.mockResolvedValue({ ...GRAPH, truncated: true });
    wrap();
    const note = await screen.findByTestId("link-graph-truncated");
    expect(note.textContent).toMatch(/ausgeblendet|hidden/);
  });

  it("lays nodes out in one column per depth", () => {
    const { pos } = layoutGraph(GRAPH.nodes as never);
    expect(pos.get("ticket:7")!.x).toBe(0);
    expect(pos.get("ticket:9")!.x).toBe(pos.get("ci:znuny:45")!.x);
    expect(pos.get("ticket:9")!.y).toBeLessThan(pos.get("ci:znuny:45")!.y);
    expect(nodeHref(GRAPH.nodes[1] as never)).toBe("/agent/tickets/9");
  });
});
