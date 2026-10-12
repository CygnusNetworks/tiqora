import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { SegmentedControl } from "./SegmentedControl";

const items = [
  { value: "form", label: "Formular" },
  { value: "hide", label: "Ausblenden" },
  { value: "ai", label: "KI antwortet allein", disabled: true },
];

describe("SegmentedControl", () => {
  it("the checked option is the one tab stop", () => {
    render(<SegmentedControl items={items} value="hide" onChange={() => undefined} testId="seg" />);
    expect(screen.getByTestId("seg-hide").tabIndex).toBe(0);
    expect(screen.getByTestId("seg-form").tabIndex).toBe(-1);
    expect(screen.getByTestId("seg-ai").tabIndex).toBe(-1);
  });

  it("a checked option that became disabled hands the tab stop to the first enabled one", () => {
    const onChange = vi.fn();
    render(<SegmentedControl items={items} value="ai" onChange={onChange} testId="seg" />);
    const first = screen.getByTestId("seg-form");
    expect(screen.getByTestId("seg-ai").tabIndex).toBe(-1);
    expect(first.tabIndex).toBe(0);
    // and the keyboard works from there
    fireEvent.keyDown(first, { key: "ArrowRight" });
    expect(onChange).toHaveBeenCalledWith("hide");
  });

  it("without a value the first enabled option is the tab stop", () => {
    const later = [{ value: "a", label: "A", disabled: true }, ...items];
    render(<SegmentedControl items={later} value={null} onChange={() => undefined} testId="seg" />);
    expect(screen.getByTestId("seg-a").tabIndex).toBe(-1);
    expect(screen.getByTestId("seg-form").tabIndex).toBe(0);
  });
});
