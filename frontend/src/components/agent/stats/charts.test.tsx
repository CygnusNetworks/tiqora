import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { createInstance } from "i18next";
import { I18nextProvider } from "react-i18next";
import de from "@/i18n/locales/de.json";
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

  it("heatmap keeps its natural width inside a scroll wrapper and anchors the tooltip on the hovered cell", () => {
    const rect = (left: number, top: number, width: number, height: number) =>
      ({ left, top, width, height, right: left + width, bottom: top + height, x: left, y: top }) as DOMRect;
    const spy = vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
      return this.getAttribute("data-testid") === "hm-cell-1-2" ? rect(200, 160, 32, 24) : rect(20, 100, 330, 300);
    });
    try {
      render(
        <HeatmapChart
          testId="hm"
          rows={[{ key: "r0", label: "Mo" }, { key: "r1", label: "Di" }]}
          columns={[{ key: "a", label: "8" }, { key: "b", label: "9" }, { key: "c", label: "10" }]}
          values={[[1, 2, 3], [4, 5, 6]]}
        />,
      );
      const svg = document.querySelector("svg") as SVGSVGElement;
      expect(svg.style.minWidth).toBe(`${92 + 3 * 32}px`);
      expect(svg.parentElement?.className).toContain("overflow-x-auto");
      fireEvent.mouseEnter(screen.getByTestId("hm-cell-1-2"));
      const tip = screen.getByTestId("hm-tooltip");
      // cell centre 216 minus the frame's left edge 20; cell top 160 minus frame top 100, minus 2
      expect(tip.style.left).toBe("196px");
      expect(tip.style.top).toBe("58px");
    } finally {
      spy.mockRestore();
    }
  });

  it("heatmap legend names the ramp ends", () => {
    render(
      <HeatmapChart testId="hm" rows={[{ key: "r", label: "Mo" }]} columns={[{ key: "a", label: "8" }]} values={[[42]]} />,
    );
    expect(screen.getByTestId("hm-legend")).toHaveTextContent("0");
    expect(screen.getByTestId("hm-legend")).toHaveTextContent("42");
  });

  it("line chart labels the x axis at a handful of evenly spaced buckets, not just the ends", () => {
    const labels = Array.from({ length: 30 }, (_, i) => `T${i}`);
    render(<LineChart testId="lc" labels={labels} series={[{ name: "A", color: seriesColor(0), values: labels.map((_, i) => i) }]} />);
    const shown = Array.from(screen.getByTestId("lc-xlabels").querySelectorAll("span")).map((n) => n.textContent);
    expect(shown).toEqual(["T0", "T7", "T15", "T22", "T29"]);
    // two buckets: both ends, nothing else
    const { unmount } = render(<LineChart testId="l2" labels={["a", "b"]} series={[{ name: "A", color: seriesColor(0), values: [1, 2] }]} />);
    expect(Array.from(screen.getByTestId("l2-xlabels").querySelectorAll("span")).map((n) => n.textContent)).toEqual(["a", "b"]);
    unmount();
  });

  it("a bar without data reads '—' and has no bar", () => {
    render(<BarChart testId="bc" data={[{ label: "A", value: 4 }, { label: "B", value: null }]} />);
    expect(screen.getByTestId("bc-hit-1")).toHaveAttribute("aria-label", "B: —");
    expect(screen.getByTestId("bc-bar-1").getAttribute("d")).not.toContain("NaN");
    expect(screen.getByTestId("bc")).toHaveTextContent("—");
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

  it("kpi delta in percentage points prints the difference of the rates", () => {
    render(<KpiTile label="T" value="22 %" delta={{ value: 0.02, unit: "points", direction: "up", goodWhen: "up" }} testId="k" />);
    expect(screen.getByTestId("k-delta")).toHaveTextContent("+2.0 pp");
    expect(screen.getByTestId("k-delta")).toHaveClass("text-green");
  });

  it("kpi delta is green when the direction is the good one and muted when flat", () => {
    const { rerender } = render(
      <KpiTile label="T" value="1" delta={{ value: -0.05, direction: "down", goodWhen: "down" }} testId="k" />,
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

  it("line chart hit areas are focusable, show the tooltip on focus and carry the values", () => {
    render(<LineChart testId="lc" labels={["KW 1", "KW 2"]} series={twoSeries} />);
    const hit = screen.getByTestId("lc-hit-1");
    expect(hit).toHaveAttribute("tabindex", "-1");
    expect(hit).toHaveAttribute("role", "img");
    expect(hit).toHaveAttribute("aria-label", expect.stringContaining("Erstellt 5"));
    fireEvent.focus(hit);
    expect(screen.getByTestId("lc-tooltip")).toHaveTextContent("KW 2");
    fireEvent.blur(hit);
    expect(screen.queryByTestId("lc-tooltip")).toBeNull();
  });

  it("stacked bar, bar, donut and heatmap targets are focusable with value labels", () => {
    render(
      <>
        <StackedBarChart
          testId="sb"
          categories={[{ key: "c1", label: "Mo" }]}
          series={[{ key: "s1", label: "Eingang", color: seriesColor(0), values: [2] }]}
        />
        <BarChart testId="bc" data={[{ label: "A", value: 4 }]} />
        <DonutChart testId="dn" data={[{ key: "a", label: "Offen", value: 3, color: seriesColor(0) }]} />
        <HeatmapChart testId="hm" rows={[{ key: "r", label: "Mo" }]} columns={[{ key: "c", label: "9" }]} values={[[5]]} />
      </>,
    );
    for (const id of ["sb-hit-0", "bc-hit-0", "dn-slice-a"]) {
      expect(screen.getByTestId(id)).toHaveAttribute("tabindex", "0");
    }
    // dense grids stay out of the tab order but keep their label
    expect(screen.getByTestId("hm-cell-0-0")).toHaveAttribute("tabindex", "-1");
    expect(screen.getByTestId("hm-cell-0-0")).toHaveAttribute("role", "img");
    expect(screen.getByTestId("hm-cell-0-0")).toHaveAttribute("aria-label", "Mo · 9: 5");
    expect(screen.getByTestId("sb-hit-0")).toHaveAttribute("aria-label", "Mo: Eingang 2");
    expect(screen.getByTestId("bc-hit-0")).toHaveAttribute("aria-label", "A: 4");
    fireEvent.focus(screen.getByTestId("bc-hit-0"));
    expect(screen.getByTestId("bc-tooltip")).toBeInTheDocument();
    fireEvent.focus(screen.getByTestId("hm-cell-0-0"));
    expect(screen.getByTestId("hm-tooltip")).toHaveTextContent("Mo · 9");
    expect(screen.getByRole("group", { name: "A: 4" })).toBeInTheDocument();
  });

  it("heatmap value text uses fixed colours per ramp step", () => {
    render(
      <HeatmapChart
        testId="hm"
        rows={[{ key: "r", label: "Mo" }]}
        columns={[{ key: "a", label: "8" }, { key: "b", label: "9" }]}
        values={[[1, 10]]}
      />,
    );
    const fills = Array.from(document.querySelectorAll("text.font-mono")).map((n) => (n as SVGElement).style.fill);
    expect(fills).toEqual(["#0d1b2e", "#ffffff"]);
  });

  it("a single bucket draws a persistent marker per series", () => {
    render(<LineChart testId="lc" labels={["KW 1"]} series={twoSeries.map((s) => ({ ...s, values: [s.values[0]] }))} />);
    expect(screen.getByTestId("lc-marker-Erstellt")).toBeInTheDocument();
    expect(screen.getByTestId("lc-marker-Geschlossen")).toBeInTheDocument();
  });

  it("line chart with a negative value gets an axis label below zero", () => {
    render(<LineChart testId="lc" labels={["a", "b"]} series={[{ name: "Saldo", color: seriesColor(0), values: [-4, 6] }]} formatValue={(v) => `v${v}`} />);
    expect(screen.getByTestId("lc")).toHaveTextContent(/v-\d/);
  });

  it("bar and stacked bar clamp negatives and never draw an inverted rect", () => {
    render(
      <>
        <BarChart testId="bc" data={[{ label: "A", value: -3 }, { label: "B", value: 2 }]} />
        <StackedBarChart
          testId="sb"
          categories={[{ key: "c1", label: "Mo" }]}
          series={[
            { key: "s1", label: "A", color: seriesColor(0), values: [-2] },
            { key: "s2", label: "B", color: seriesColor(1), values: [3] },
          ]}
        />
      </>,
    );
    const heights = (id: string) => screen.getByTestId(id).getAttribute("d") ?? "";
    expect(heights("bc-bar-0")).not.toMatch(/-\d/);
    expect(heights("bc-bar-1")).not.toMatch(/-\d/);
    expect(screen.queryByTestId("sb-seg-c1-s1")).toBeNull();
    expect(heights("sb-seg-c1-s2")).not.toMatch(/-\d/);
  });

  it("kpi tile renders a negative delta as a decrease", () => {
    render(<KpiTile label="T" value="1" delta={{ value: -0.08, direction: "down", goodWhen: "down" }} testId="k" />);
    const d = screen.getByTestId("k-delta");
    expect(d).toHaveTextContent("−8");
    expect(d).toHaveTextContent("Decrease");
    expect(d).toHaveClass("text-green");
  });

  it("all-zero data renders without NaN", () => {
    render(
      <>
        <LineChart testId="lc" labels={["a", "b"]} series={[{ name: "Z", color: seriesColor(0), values: [0, 0] }]} />
        <BarChart testId="bc" data={[{ label: "A", value: 0 }]} />
      </>,
    );
    expect(screen.getByTestId("lc-series-Z").getAttribute("d")).not.toContain("NaN");
    expect(screen.getByTestId("bc-bar-0").getAttribute("d")).not.toContain("NaN");
  });

  it("long labels are truncated with the full text in a title", () => {
    const long = "Eine sehr lange Warteschlange mit vielen Worten";
    render(
      <>
        <HeatmapChart testId="hm" rows={[{ key: "r", label: long }]} columns={[{ key: "c", label: "9" }]} values={[[1]]} />
        <LineChart testId="lc" labels={["a", "b"]} series={[{ name: long, color: seriesColor(0), values: [1, 2] }, twoSeries[1]]} />
      </>,
    );
    expect(screen.getByTestId("hm")).toHaveTextContent("…");
    expect(Array.from(screen.getByTestId("hm").querySelectorAll("title")).map((n) => n.textContent)).toContain(long);
    const item = screen.getByTestId("lc-legend").querySelector("li");
    expect(item).toHaveAttribute("title", long);
    expect(item?.querySelector(".truncate")).not.toBeNull();
  });

  it("uses the app locale for percentages (de) and keeps legend labels translated", async () => {
    const i18n = createInstance();
    await i18n.init({ lng: "de", resources: { de: { translation: de } }, interpolation: { escapeValue: false } });
    render(
      <I18nextProvider i18n={i18n}>
        <DonutChart
          testId="dn"
          data={[
            { key: "a", label: "Offen", value: 1, color: seriesColor(0) },
            { key: "b", label: "Zu", value: 7, color: seriesColor(1) },
          ]}
        />
        <KpiTile label="T" value="1" delta={{ value: 0.125, direction: "up", goodWhen: "up" }} testId="k" />
      </I18nextProvider>,
    );
    expect(screen.getByTestId("dn-legend")).toHaveTextContent("12,5 %");
    expect(screen.getByTestId("dn-legend")).toHaveAttribute("aria-label", "Legende");
    expect(screen.getByTestId("k-delta")).toHaveTextContent("+12,5 %");
    expect(screen.getByTestId("k-delta")).toHaveTextContent("Anstieg");
  });

  it("sparkline is decorative and safe without values", () => {
    render(<Sparkline values={[]} testId="sp" />);
    const svg = screen.getByTestId("sp");
    expect(svg).toHaveAttribute("aria-hidden", "true");
    expect(svg).not.toHaveAttribute("role");
    expect(svg.querySelector("polyline")).toBeNull();
  });

  it("stacked bar leaves the tab order above 12 categories", () => {
    const cats = (n: number) => Array.from({ length: n }, (_, i) => ({ key: `c${i}`, label: `K${i}` }));
    const ser = (n: number) => [{ key: "s", label: "S", color: seriesColor(0), values: Array.from({ length: n }, () => 1) }];
    const { rerender } = render(<StackedBarChart testId="sb" categories={cats(12)} series={ser(12)} />);
    expect(screen.getByTestId("sb-hit-0")).toHaveAttribute("tabindex", "0");
    rerender(<StackedBarChart testId="sb" categories={cats(13)} series={ser(13)} />);
    expect(screen.getByTestId("sb-hit-0")).toHaveAttribute("tabindex", "-1");
    expect(screen.getByTestId("sb-hit-0")).toHaveAttribute("role", "img");
    expect(screen.getByTestId("sb-hit-0")).toHaveAttribute("aria-label", "K0: S 1");
  });

  it("kpi arrow and wording follow the sign of the value, colour follows direction", () => {
    render(<KpiTile label="T" value="1" delta={{ value: 0.2, direction: "down", goodWhen: "down" }} testId="k" />);
    const d = screen.getByTestId("k-delta");
    expect(d).toHaveTextContent("Increase");
    expect(d).toHaveTextContent("+20");
    expect(d).not.toHaveTextContent("−");
    expect(d.querySelector("path")?.getAttribute("d")).toBe("M6 2 L10 8 H2 Z");
    expect(d).toHaveClass("text-green");
  });
});
