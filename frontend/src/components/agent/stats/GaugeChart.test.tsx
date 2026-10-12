import { afterEach, beforeAll, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { GaugeChart } from "./GaugeChart";
import { gaugeTone } from "./gaugeTone";

beforeAll(async () => {
  await i18n.changeLanguage("de");
});
afterEach(cleanup);

const gauge = (value: number | null, target?: number) =>
  render(
    <I18nextProvider i18n={i18n}>
      <GaugeChart value={value} target={target} label="SLA-Erfüllung" testId="g" />
    </I18nextProvider>,
  );

describe("gaugeTone", () => {
  it("is success at or above the target, warn within 5 points below, danger beyond", () => {
    expect(gaugeTone(90, 90)).toBe("success");
    expect(gaugeTone(97, 90)).toBe("success");
    expect(gaugeTone(89.9, 90)).toBe("warn");
    expect(gaugeTone(85, 90)).toBe("warn");
    expect(gaugeTone(84.9, 90)).toBe("danger");
    expect(gaugeTone(10, 90)).toBe("danger");
    expect(gaugeTone(50)).toBe("neutral");
  });
});

describe("GaugeChart", () => {
  it("labels the image with value and target and draws a target tick", () => {
    gauge(87, 90);
    const img = screen.getByRole("img", { name: "SLA-Erfüllung 87 %, Ziel 90 %" });
    expect(img).toHaveAttribute("data-tone", "warn");
    expect(screen.getByTestId("g-target")).toBeInTheDocument();
    expect(screen.getByTestId("g-target-label")).toHaveTextContent("Ziel 90 %");
  });

  it("colours the arc by tone", () => {
    const { rerender } = gauge(95, 90);
    expect(screen.getByRole("img")).toHaveAttribute("data-tone", "success");
    expect(screen.getByTestId("g-arc")).toHaveAttribute("stroke", "var(--color-green)");
    rerender(
      <I18nextProvider i18n={i18n}>
        <GaugeChart value={60} target={90} label="SLA-Erfüllung" testId="g" />
      </I18nextProvider>,
    );
    expect(screen.getByRole("img")).toHaveAttribute("data-tone", "danger");
    expect(screen.getByTestId("g-arc")).toHaveAttribute("stroke", "var(--color-danger)");
  });

  it("formats with the given formatter and without a target omits the tick", () => {
    render(<GaugeChart value={0.5} label="Anteil" formatValue={(v) => `${v * 100} Prozent`} testId="g" />);
    expect(screen.getByRole("img", { name: "Anteil 50 Prozent" })).toBeInTheDocument();
    expect(screen.queryByTestId("g-target")).toBeNull();
  });

  it("shows the muted empty text for null and no image", () => {
    gauge(null, 90);
    expect(screen.getByTestId("g")).toHaveTextContent("Keine Tickets im Zeitraum");
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("clamps out-of-range values to the arc", () => {
    gauge(140, 90);
    expect(screen.getByRole("img", { name: /140/ })).toBeInTheDocument();
    expect(screen.getByTestId("g-arc").getAttribute("d")).toMatch(/^M /);
  });
});
