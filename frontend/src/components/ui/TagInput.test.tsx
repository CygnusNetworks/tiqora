import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { TagInput } from "./TagInput";

const SUGGESTIONS = [
  { name: "vpn", count: 4 },
  { name: "wlan", count: 2 },
  { name: "vertrag", count: 0 },
];

describe("TagInput", () => {
  it("renders pills and removes on ✕", () => {
    const onChange = vi.fn();
    render(<TagInput value={["vpn", "wlan"]} onChange={onChange} testId="tags" />);
    expect(screen.getByTestId("tags-pill-vpn")).toBeTruthy();
    fireEvent.click(screen.getByTestId("tags-remove-wlan"));
    expect(onChange).toHaveBeenCalledWith(["vpn"]);
  });

  it("filters suggestions while typing and adds on click", () => {
    const onChange = vi.fn();
    render(
      <TagInput value={[]} onChange={onChange} suggestions={SUGGESTIONS} testId="tags" />,
    );
    fireEvent.change(screen.getByTestId("tags-input"), { target: { value: "v" } });
    expect(screen.getByTestId("tags-option-vpn")).toBeTruthy();
    expect(screen.getByTestId("tags-option-vertrag")).toBeTruthy();
    expect(screen.queryByTestId("tags-option-wlan")).toBeNull();
    fireEvent.click(screen.getByTestId("tags-option-vpn"));
    expect(onChange).toHaveBeenCalledWith(["vpn"]);
  });

  it("excludes already-selected tags from suggestions", () => {
    render(
      <TagInput value={["vpn"]} onChange={vi.fn()} suggestions={SUGGESTIONS} testId="tags" />,
    );
    fireEvent.focus(screen.getByTestId("tags-input"));
    expect(screen.queryByTestId("tags-option-vpn")).toBeNull();
    expect(screen.getByTestId("tags-option-wlan")).toBeTruthy();
  });

  it("adds free text on Enter and comma, deduplicates case-insensitively", () => {
    const onChange = vi.fn();
    const { rerender } = render(<TagInput value={[]} onChange={onChange} testId="tags" />);
    const input = screen.getByTestId("tags-input");
    fireEvent.change(input, { target: { value: "glasfaser" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith(["glasfaser"]);

    rerender(<TagInput value={["glasfaser"]} onChange={onChange} testId="tags" />);
    fireEvent.change(input, { target: { value: "Glasfaser" } });
    fireEvent.keyDown(input, { key: "Enter" });
    // duplicate (case-insensitive) → no second onChange call with 2 items
    expect(onChange).not.toHaveBeenCalledWith(["glasfaser", "Glasfaser"]);
  });

  it("keyboard: ArrowDown+Enter picks the highlighted suggestion", () => {
    const onChange = vi.fn();
    render(
      <TagInput value={[]} onChange={onChange} suggestions={SUGGESTIONS} testId="tags" />,
    );
    const input = screen.getByTestId("tags-input");
    fireEvent.focus(input);
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith(["wlan"]);
  });

  it("Backspace on empty input removes the last pill", () => {
    const onChange = vi.fn();
    render(<TagInput value={["vpn", "wlan"]} onChange={onChange} testId="tags" />);
    fireEvent.keyDown(screen.getByTestId("tags-input"), { key: "Backspace" });
    expect(onChange).toHaveBeenCalledWith(["vpn"]);
  });
});
