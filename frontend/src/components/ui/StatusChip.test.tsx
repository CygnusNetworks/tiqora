import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { PriorityChip, SoftChip, StateChip } from "./StatusChip";

describe("SoftChip", () => {
  it("renders the label and applies the colour var as text colour", () => {
    render(<SoftChip color="var(--color-state-open)">Open</SoftChip>);
    const chip = screen.getByText("Open");
    expect(chip).toHaveStyle({ color: "var(--color-state-open)" });
    expect(chip.style.background).toContain("color-mix");
    expect(chip.style.background).toContain("var(--color-state-open)");
    expect(chip.style.borderRadius).toBe("999px");
    expect(chip.style.fontWeight).toBe("650");
  });
});

describe("StateChip", () => {
  it("renders the localised state label with the matching state colour", () => {
    render(
      <I18nextProvider i18n={i18n}>
        <StateChip state="open" data-testid="state-chip" />
      </I18nextProvider>,
    );
    const chip = screen.getByTestId("state-chip");
    expect(chip).toHaveTextContent("Open");
    expect(chip).toHaveAttribute("data-kind", "state");
    expect(chip).toHaveStyle({ color: "var(--color-state-open)" });
  });

  it("renders nothing when state is empty", () => {
    const { container } = render(
      <I18nextProvider i18n={i18n}>
        <StateChip state={null} />
      </I18nextProvider>,
    );
    expect(container).toBeEmptyDOMElement();
  });
});

describe("PriorityChip", () => {
  it("strips the numeric rank and applies the priority colour ramp", () => {
    render(
      <PriorityChip
        priority="5 very high"
        priorityId={5}
        data-testid="prio-chip"
      />,
    );
    const chip = screen.getByTestId("prio-chip");
    expect(chip).toHaveTextContent("very high");
    expect(chip).not.toHaveTextContent("5 very");
    expect(chip).toHaveAttribute("data-kind", "priority");
    expect(chip).toHaveStyle({ color: "var(--color-prio-5)" });
  });

  it("falls back to prio-3 colour when id is null", () => {
    render(
      <PriorityChip priority="3 normal" priorityId={null} data-testid="prio-chip" />,
    );
    expect(screen.getByTestId("prio-chip")).toHaveStyle({
      color: "var(--color-prio-3)",
    });
  });
});
