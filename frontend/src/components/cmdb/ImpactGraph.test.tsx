import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import i18n from "@/i18n";
import { createDemoCmdb, type DemoCmdb } from "@/demo/cmdbMock";
import type { ImpactGraph as ImpactGraphData } from "@/lib/cmdbApi";
import { renderWithRouter, serveCmdb, type Seen } from "@/test/cmdbHarness";
import { ImpactGraph } from "./ImpactGraph";

beforeAll(() => {
  // React Flow measures nodes with ResizeObserver and reads the viewport matrix; jsdom has neither.
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  vi.stubGlobal("ResizeObserver", RO);
  vi.stubGlobal(
    "DOMMatrixReadOnly",
    class {
      m22 = 1;
      constructor(_t?: string) {}
    },
  );
});

function setViewport(width: number) {
  window.matchMedia = ((query: string) => ({
    matches: width <= 767 && /max-width/.test(query),
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  })) as unknown as typeof window.matchMedia;
}

let store: DemoCmdb;
const seen: Seen[] = [];
let graphOverride: ImpactGraphData | null = null;

beforeEach(async () => {
  store = createDemoCmdb();
  seen.length = 0;
  graphOverride = null;
  await i18n.changeLanguage("de");
  serveCmdb(store, seen, (method, path) => (graphOverride && method === "GET" && /\/impact$/.test(path) ? graphOverride : undefined));
});
afterEach(() => {
  vi.restoreAllMocks();
  // @ts-expect-error restore the jsdom default (no matchMedia)
  delete window.matchMedia;
});

describe("ImpactGraph", () => {
  it("renders the text list instead of the canvas at 390 px", async () => {
    setViewport(390);
    await renderWithRouter(<ImpactGraph ciId={1} />);
    const list = await screen.findByTestId("impact-list");
    expect(list).not.toHaveClass("sr-only");
    expect(screen.queryByTestId("impact-canvas")).toBeNull();
    // root with its neighbours nested below it
    expect(within(list).getByTestId("impact-item-ci:1")).toHaveTextContent("web-01");
    expect(within(list).getByTestId("impact-item-ci:3")).toHaveTextContent("db-01");
  });

  it("draws the canvas on wide screens and keeps the list for screen readers", async () => {
    setViewport(1280);
    await renderWithRouter(<ImpactGraph ciId={1} />);
    expect(await screen.findByTestId("impact-canvas")).toBeInTheDocument();
    const node = await screen.findByTestId("impact-node-ci:3");
    expect(node).toHaveTextContent("db-01");
    expect(screen.getByTestId("impact-node-ci:1")).toHaveTextContent("web-01");
    expect(screen.getByTestId("impact-list")).toHaveClass("sr-only");
    expect(within(screen.getByTestId("impact-list")).getByTestId("impact-item-ci:3")).toBeInTheDocument();
  });

  it("names a CI the viewer may not read 'Kein Zugriff' and never its number", async () => {
    setViewport(390);
    await renderWithRouter(<ImpactGraph ciId={3} />);
    const hidden = await screen.findByTestId("impact-item-ci:99");
    expect(hidden).toHaveTextContent("Kein Zugriff");
    expect(within(hidden).queryByRole("link")).toBeNull();
  });

  it("shows the 'Ausschnitt gekürzt' banner only for a truncated graph", async () => {
    setViewport(390);
    await renderWithRouter(<ImpactGraph ciId={1} />);
    await screen.findByTestId("impact-list");
    expect(screen.queryByTestId("impact-truncated")).toBeNull();
  });

  it("shows the banner when the server cut the graph", async () => {
    setViewport(390);
    graphOverride = {
      root: "ci:1",
      truncated: true,
      nodes: [
        { id: "ci:1", kind: "ci", ci_id: 1, number: "SRV-0001", name: "web-01", hidden: false, depth: 0 },
        { id: "ci:2", kind: "ci", ci_id: 2, number: "SRV-0002", name: "app-01", hidden: false, depth: 1 },
      ],
      edges: [{ source: "ci:1", target: "ci:2", link_type: "DependsOn", propagates: true }],
    } as ImpactGraphData;
    await renderWithRouter(<ImpactGraph ciId={1} />);
    expect(await screen.findByTestId("impact-truncated")).toHaveTextContent("Ausschnitt gekürzt");
  });

  it("asks the server for another depth and direction", async () => {
    setViewport(390);
    await renderWithRouter(<ImpactGraph ciId={1} />);
    await screen.findByTestId("impact-list");
    expect(seen.find((r) => /\/impact$/.test(r.path))?.query.get("depth")).toBe("2");
    expect(seen.find((r) => /\/impact$/.test(r.path))?.query.get("direction")).toBe("both");
    fireEvent.click(screen.getByTestId("impact-depth-4"));
    fireEvent.click(screen.getByTestId("impact-direction-down"));
    await waitFor(() => {
      const last = [...seen].reverse().find((r) => /\/impact$/.test(r.path));
      expect(last?.query.get("depth")).toBe("4");
      expect(last?.query.get("direction")).toBe("down");
    });
  });

  it("says so when the CI has no neighbours", async () => {
    setViewport(390);
    await renderWithRouter(<ImpactGraph ciId={6} />);
    expect(await screen.findByTestId("impact-empty")).toBeInTheDocument();
    expect(screen.queryByTestId("impact-list")).toBeNull();
  });
});
