import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { useState } from "react";
import { OtpInput } from "./OtpInput";

/** Thin controlled wrapper so tests can drive the component like a real parent. */
function Harness({
  onComplete,
  status,
  statusMessage,
}: {
  onComplete?: (v: string) => void;
  status?: "idle" | "verifying" | "error" | "success";
  statusMessage?: string;
}) {
  const [value, setValue] = useState("");
  return (
    <OtpInput
      value={value}
      onChange={setValue}
      onComplete={onComplete}
      status={status}
      statusMessage={statusMessage}
      data-testid="otp"
      aria-label="Code"
    />
  );
}

describe("OtpInput", () => {
  it("keeps only digits and caps length at 6", () => {
    render(<Harness />);
    const input = screen.getByTestId("otp") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "12ab34" } });
    expect(input.value).toBe("1234");
    fireEvent.change(input, { target: { value: "123456789" } });
    expect(input.value).toBe("123456");
  });

  it("renders one visible cell per digit position", () => {
    render(<Harness />);
    expect(screen.getAllByTestId(/^otp-cell-/)).toHaveLength(6);
  });

  it("mirrors entered digits into the cells", () => {
    render(<Harness />);
    fireEvent.change(screen.getByTestId("otp"), { target: { value: "123" } });
    expect(screen.getByTestId("otp-cell-0")).toHaveTextContent("1");
    expect(screen.getByTestId("otp-cell-2")).toHaveTextContent("3");
    expect(screen.getByTestId("otp-cell-3")).toHaveTextContent("");
  });

  it("fires onComplete exactly once when the 6th digit is entered", () => {
    const onComplete = vi.fn();
    render(<Harness onComplete={onComplete} />);
    const input = screen.getByTestId("otp");
    fireEvent.change(input, { target: { value: "12345" } });
    expect(onComplete).not.toHaveBeenCalled();
    fireEvent.change(input, { target: { value: "123456" } });
    expect(onComplete).toHaveBeenCalledTimes(1);
    expect(onComplete).toHaveBeenCalledWith("123456");
  });

  it("accepts a full pasted value via a single change event (test-driver path)", () => {
    const onComplete = vi.fn();
    render(<Harness onComplete={onComplete} />);
    fireEvent.change(screen.getByTestId("otp"), { target: { value: "654321" } });
    expect(onComplete).toHaveBeenCalledWith("654321");
  });

  it("shows the status message when provided", () => {
    render(<Harness status="error" statusMessage="Falscher Code" />);
    expect(screen.getByTestId("otp-status")).toHaveTextContent("Falscher Code");
  });

  it("marks the input invalid while in the error state", () => {
    render(<Harness status="error" statusMessage="x" />);
    expect(screen.getByTestId("otp")).toHaveAttribute("aria-invalid", "true");
  });
});
