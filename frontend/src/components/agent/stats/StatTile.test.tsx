import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatTile } from "./StatTile";
import { BarChart } from "./BarChart";
import { LineChart } from "./LineChart";

describe("StatTile", () => {
  it("renders label and value", () => {
    render(<StatTile label="Tickets" value={42} testId="tile-tickets" />);
    const tile = screen.getByTestId("tile-tickets");
    expect(tile).toBeInTheDocument();
    expect(tile).toHaveTextContent("Tickets");
    expect(tile).toHaveTextContent("42");
  });

  it("applies the danger tone class", () => {
    render(<StatTile label="Escalated" value={3} tone="danger" testId="tile-escalated" />);
    const value = screen.getByText("3");
    expect(value.className).toContain("text-danger");
  });
});

describe("BarChart", () => {
  it("renders one bar per datum", () => {
    render(
      <BarChart
        testId="chart-bars"
        data={[
          { label: "Support", value: 5 },
          { label: "Sales", value: 2 },
        ]}
      />,
    );
    expect(screen.getByTestId("chart-bars-bar-0")).toBeInTheDocument();
    expect(screen.getByTestId("chart-bars-bar-1")).toBeInTheDocument();
    expect(screen.getByText("Support")).toBeInTheDocument();
  });

  it("shows the empty state when there is no data", () => {
    render(<BarChart testId="chart-empty" data={[]} emptyLabel="Nothing here" />);
    expect(screen.getByTestId("chart-empty")).toHaveTextContent("Nothing here");
  });
});

describe("LineChart", () => {
  it("renders a polyline per series", () => {
    render(
      <LineChart
        testId="chart-lines"
        labels={["2024-01-01", "2024-01-02"]}
        series={[
          { name: "Created", color: "#000", values: [1, 2] },
          { name: "Closed", color: "#111", values: [0, 1] },
        ]}
      />,
    );
    expect(screen.getByTestId("chart-lines-series-Created")).toBeInTheDocument();
    expect(screen.getByTestId("chart-lines-series-Closed")).toBeInTheDocument();
    expect(screen.getByText("Created")).toBeInTheDocument();
  });

  it("shows the empty state when there are no labels", () => {
    render(<LineChart testId="chart-lines-empty" labels={[]} series={[]} emptyLabel="No points" />);
    expect(screen.getByTestId("chart-lines-empty")).toHaveTextContent("No points");
  });
});
