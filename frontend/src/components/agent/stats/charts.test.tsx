import { describe, it, expect } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { LineChart } from "./LineChart";
import { BarChart } from "./BarChart";
import { StackedBarChart } from "./StackedBarChart";
import { DonutChart } from "./DonutChart";
import { HeatmapChart } from "./HeatmapChart";
import { KpiTile } from "./KpiTile";
import { Sparkline } from "./Sparkline";
import { seriesColor } from "./chartColors";

const twoSeries = [
  { name: "Erstellt", color: seriesColor(0), values: [3, 5] },
  { name: "Geschlossen", color: seriesColor(1), values: [2, 4] },
];

describe("charts", () => {
  it("line chart shows a legend for two series and a tooltip on hover", async () => {
    render(<LineChart testId="lc" labels={["KW 1", "KW 2"]} series={twoSeries} />);
    expect(screen.getByTestId("lc-legend")).toHaveTextContent("Erstellt");
    expect(screen.queryByTestId("lc-tooltip")).toBeNull();
    fireEvent.mouseMove(screen.getByTestId("lc-hit-1"));
    const tip = await screen.findByTestId("lc-tooltip");
    expect(tip).toHaveTextContent("KW 2");
    expect(tip).toHaveTextContent("5");
    expect(tip).toHaveTextContent("Geschlossen");
    expect(screen.getByTestId("lc-crosshair")).toBeInTheDocument();
  });

  it("single-series line has no legend box", () => {
    render(<LineChart testId="lc" labels={["a", "b"]} series={[twoSeries[0]]} />);
    expect(screen.getByTestId("lc-series-Erstellt")).toBeInTheDocument();
    expect(screen.queryByTestId("lc-legend")).toBeNull();
  });

  it("area mode fills under the line", () => {
    const { rerender } = render(<LineChart testId="lc" labels={["a", "b"]} series={[twoSeries[0]]} />);
    expect(screen.queryByTestId("lc-area-Erstellt")).toBeNull();
    rerender(<LineChart testId="lc" labels={["a", "b"]} series={[twoSeries[0]]} area />);
    const area = screen.getByTestId("lc-area-Erstellt");
    expect(area.tagName.toLowerCase()).toBe("path");
    expect(area).toHaveAttribute("fill", seriesColor(0));
  });

  it("stacked bar renders segments with a surface gap and stable order", () => {
    render(
      <StackedBarChart
        testId="sb"
        categories={[
          { key: "c1", label: "Mo" },
          { key: "c2", label: "Di" },
        ]}
        series={[
          { key: "s1", label: "Eingang", color: seriesColor(0), values: [2, 3] },
          { key: "s2", label: "Ausgang", color: seriesColor(1), values: [1, null] },
        ]}
      />,
    );
    const seg = screen.getByTestId("sb-seg-c1-s1");
    expect(seg.tagName.toLowerCase()).toBe("path");
    expect(seg).toHaveAttribute("stroke", "var(--color-surface)");
    expect(seg).toHaveAttribute("stroke-width", "2");
    expect(screen.getByTestId("sb-seg-c1-s2")).toBeInTheDocument();
    expect(screen.getByTestId("sb-seg-c2-s1")).toBeInTheDocument();
    // null value draws no segment
    expect(screen.queryByTestId("sb-seg-c2-s2")).toBeNull();
    // s1 sits below s2 in category c1 (smaller y = higher up)
    const top = (id: string) => Number(screen.getByTestId(id).getAttribute("d")?.match(/M[\d.]+,([\d.]+)/)?.[1]);
    expect(top("sb-seg-c1-s2")).toBeLessThan(top("sb-seg-c1-s1"));
    expect(screen.getByTestId("sb-legend")).toHaveTextContent("Ausgang");
    fireEvent.mouseEnter(screen.getByTestId("sb-hit-0"));
    expect(screen.getByTestId("sb-tooltip")).toHaveTextContent("Mo");
  });

  it("donut renders one arc per slice and a centre label", () => {
    render(
      <DonutChart
        testId="dn"
        centerLabel="Tickets"
        data={[
          { key: "a", label: "Offen", value: 3, color: seriesColor(0) },
          { key: "b", label: "Zu", value: 1, color: seriesColor(1) },
          { key: "c", label: "Leer", value: 0, color: seriesColor(2) },
        ]}
      />,
    );
    expect(screen.getByTestId("dn-slice-a")).toBeInTheDocument();
    expect(screen.getByTestId("dn-slice-b")).toBeInTheDocument();
    expect(screen.queryByTestId("dn-slice-c")).toBeNull();
    expect(screen.getByTestId("dn-center")).toHaveTextContent("4");
    expect(screen.getByTestId("dn-legend")).toHaveTextContent("75");
  });

  it("heatmap colours by sequential ramp and shows empty cells", () => {
    render(
      <HeatmapChart
        testId="hm"
        rows={[
          { key: "r0", label: "Mo" },
          { key: "r1", label: "Di" },
        ]}
        columns={[
          { key: "c0", label: "8" },
          { key: "c1", label: "9" },
        ]}
        values={[
          [0, 5],
          [10, null],
        ]}
      />,
    );
    expect(screen.getByTestId("hm-cell-1-0")).toHaveAttribute("fill", "var(--color-seq-7)");
    expect(screen.getByTestId("hm-cell-0-0")).toHaveAttribute("fill", "var(--color-seq-1)");
    expect(screen.getByTestId("hm-cell-1-1")).toHaveAttribute("fill", "var(--color-surface-subtle)");
    fireEvent.mouseEnter(screen.getByTestId("hm-cell-0-1"));
    expect(screen.getByTestId("hm-tooltip")).toHaveTextContent("Mo · 9");
  });

  it("kpi tile shows value, delta direction and sparkline", () => {
    render(
      <KpiTile
        label="Tickets"
        value="120"
        delta={{ value: 0.12, direction: "up", goodWhen: "down" }}
        sparkline={[1, 3, 2]}
        testId="k"
      />,
    );
    expect(screen.getByTestId("k")).toHaveTextContent("120");
    expect(screen.getByTestId("k-delta")).toHaveTextContent("+12");
    expect(screen.getByTestId("k-delta")).toHaveClass("text-danger");
    expect(screen.getByTestId("k-sparkline")).toBeInTheDocument();
  });

  it("kpi delta is green when the direction is the good one and muted when flat", () => {
    const { rerender } = render(
      <KpiTile label="T" value="1" delta={{ value: 0.05, direction: "down", goodWhen: "down" }} testId="k" />,
    );
    expect(screen.getByTestId("k-delta")).toHaveClass("text-green");
    expect(screen.getByTestId("k-delta")).toHaveTextContent("−5");
    rerender(<KpiTile label="T" value="1" delta={{ value: 0, direction: "flat", goodWhen: "up" }} testId="k" />);
    expect(screen.getByTestId("k-delta")).toHaveClass("text-muted");
  });

  it("sparkline draws one polyline point per value", () => {
    render(<Sparkline values={[1, 2, 3, 4]} testId="sp" />);
    expect(screen.getByTestId("sp").querySelector("polyline")?.getAttribute("points")?.split(" ")).toHaveLength(4);
  });

  it("bar chart keeps its old props and test ids and adds a tooltip", () => {
    render(<BarChart testId="bc" data={[{ label: "A", value: 4 }, { label: "B", value: 2 }]} />);
    expect(screen.getByTestId("bc-bar-0")).toBeInTheDocument();
    expect(screen.getByTestId("bc-bar-1")).toBeInTheDocument();
    fireEvent.mouseEnter(screen.getByTestId("bc-hit-1"));
    expect(screen.getByTestId("bc-tooltip")).toHaveTextContent("B");
  });

  it("every chart shows the empty label without data", () => {
    render(<StackedBarChart testId="e1" categories={[]} series={[]} emptyLabel="leer" />);
    render(<DonutChart testId="e2" data={[]} emptyLabel="leer" />);
    render(<HeatmapChart testId="e3" rows={[]} columns={[]} values={[]} emptyLabel="leer" />);
    for (const id of ["e1", "e2", "e3"]) expect(screen.getByTestId(id)).toHaveTextContent("leer");
  });
});
