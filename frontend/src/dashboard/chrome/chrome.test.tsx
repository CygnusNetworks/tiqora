import { beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import i18n from "@/i18n";
import { Sparkline } from "./Sparkline";
import { WidgetError } from "./WidgetError";
import { WidgetUnavailable } from "./WidgetUnavailable";
import { WidgetSkeleton } from "./WidgetSkeleton";
import { DashboardFiltersProvider } from "../filters/filtersContext";
import { WidgetChromeProvider, WidgetFrame } from "./WidgetFrame";

beforeAll(async () => {
  await i18n.changeLanguage("de");
});

/** The `x,y` pairs of the sparkline's polyline. */
function polylinePoints(container: HTMLElement): number[][] {
  const raw = container.querySelector("polyline")!.getAttribute("points")!;
  return raw.split(" ").map((p) => p.split(",").map(Number));
}

describe("WidgetError", () => {
  it("shows the message and calls only its own retry", () => {
    const onRetry = vi.fn();
    render(<WidgetError message="Diese Daten konnten nicht geladen werden." onRetry={onRetry} />);
    fireEvent.click(screen.getByRole("button", { name: "Erneut versuchen" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Diese Daten konnten nicht geladen werden.")).toBeVisible();
  });
});

describe("WidgetUnavailable", () => {
  it("names the missing type and keeps a remove button only in edit mode", () => {
    const rm = vi.fn();
    render(<WidgetUnavailable typeKey="org.example.weather" mode="edit" onRemove={rm} />);
    expect(screen.getByText("Widget nicht verfügbar")).toBeVisible();
    expect(screen.getByText("org.example.weather")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Entfernen" }));
    expect(rm).toHaveBeenCalledTimes(1);
    cleanup();
    render(<WidgetUnavailable typeKey="org.example.weather" mode="view" onRemove={rm} />);
    expect(screen.getByText("Widget nicht verfügbar")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Entfernen" })).toBeNull();
  });
});

describe("Sparkline", () => {
  it("draws a polyline scaled to 96x30 and nothing for fewer than two points", () => {
    const { container } = render(<Sparkline points={[1, 2, 3]} tone="neutral" />);
    const svg = container.querySelector("svg")!;
    expect(svg.getAttribute("aria-hidden")).toBe("true");
    expect(svg.getAttribute("viewBox")).toBe("0 0 96 30");
    const pts = polylinePoints(container);
    expect(pts).toHaveLength(3);
    for (const [x, y] of pts) {
      expect(x).toBeGreaterThanOrEqual(0);
      expect(x).toBeLessThanOrEqual(96);
      expect(y).toBeGreaterThanOrEqual(0);
      expect(y).toBeLessThanOrEqual(30);
    }
    // rising series: the last point is the highest (smallest y)
    expect(pts[2][1]).toBeLessThan(pts[0][1]);
    cleanup();
    expect(render(<Sparkline points={[5]} tone="neutral" />).container.querySelector("polyline")).toBeNull();
    cleanup();
    expect(render(<Sparkline points={[]} tone="neutral" />).container.querySelector("svg")).toBeNull();
  });

  it("does not divide by zero on a flat series", () => {
    const { container } = render(<Sparkline points={[4, 4, 4]} tone="neutral" />);
    const pts = polylinePoints(container);
    expect(pts.flat().every(Number.isFinite)).toBe(true);
    expect(new Set(pts.map((p) => p[1])).size).toBe(1);
  });

  it("colours the line by tone with existing tokens", () => {
    const { container } = render(<Sparkline points={[1, 2]} tone="danger" />);
    expect(container.querySelector("polyline")).toHaveClass("stroke-danger");
  });
});

describe("WidgetSkeleton", () => {
  it("is a polite loading placeholder", () => {
    render(<WidgetSkeleton rows={3} />);
    const sk = screen.getByTestId("widget-skeleton");
    expect(sk).toHaveAttribute("aria-busy", "true");
    expect(sk.querySelectorAll("[data-skeleton-row]")).toHaveLength(3);
  });
});

describe("WidgetFrame", () => {
  it("renders the title, makes the body inert outside view mode and prefers the host's title override", () => {
    render(
      <WidgetFrame title="Queues" mode="view" testId="f1">
        <button type="button">Innen</button>
      </WidgetFrame>,
    );
    expect(screen.getByRole("heading", { name: "Queues" })).toBeVisible();
    expect(screen.getByTestId("widget-body")).not.toHaveClass("pointer-events-none");
    expect(screen.getByTestId("widget-body")).not.toHaveAttribute("inert");
    cleanup();
    render(
      <WidgetChromeProvider value={{ titleOverride: "Team-Eingang" }}>
        <WidgetFrame title="Queues" mode="edit" testId="f1">
          <button type="button">Innen</button>
        </WidgetFrame>
      </WidgetChromeProvider>,
    );
    expect(screen.getByRole("heading", { name: "Team-Eingang" })).toBeVisible();
    const body = screen.getByTestId("widget-body");
    expect(body).toHaveClass("pointer-events-none");
    expect(body).toHaveAttribute("inert");
  });

  it("shows host actions in edit mode instead of the widget's own header extras", () => {
    render(
      <WidgetChromeProvider value={{ actions: <button type="button">Einstellungen</button> }}>
        <WidgetFrame title="Team" mode="edit" testId="f2" actions={<span>2 online</span>}>
          x
        </WidgetFrame>
      </WidgetChromeProvider>,
    );
    expect(screen.getByRole("button", { name: "Einstellungen" })).toBeVisible();
    expect(screen.queryByText("2 online")).toBeNull();
  });
});

describe("WidgetFrame pin glyph", () => {
  const frame = (pinned: boolean, filters: { queue_ids: number[]; time_range: null }) =>
    render(
      <DashboardFiltersProvider filters={filters}>
        <WidgetChromeProvider value={{ pinned }}>
          <WidgetFrame title="Tickets" mode="view" testId="w">
            x
          </WidgetFrame>
        </WidgetChromeProvider>
      </DashboardFiltersProvider>,
    );

  it("shows only for a pinned widget while filters are active", () => {
    frame(true, { queue_ids: [3], time_range: null });
    expect(screen.getByLabelText("Dashboard-Filter gelten hier nicht")).toBeVisible();
    cleanup();
    frame(true, { queue_ids: [], time_range: null });
    expect(screen.queryByLabelText("Dashboard-Filter gelten hier nicht")).toBeNull();
    cleanup();
    frame(false, { queue_ids: [3], time_range: null });
    expect(screen.queryByLabelText("Dashboard-Filter gelten hier nicht")).toBeNull();
  });
});
