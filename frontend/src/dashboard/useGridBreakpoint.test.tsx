import { afterEach, describe, expect, it, vi } from "vitest";
import { useRef } from "react";
import { act, render, screen } from "@testing-library/react";
import { useGridBreakpoint } from "./useGridBreakpoint";

let report: ((width: number) => void) | null = null;
let disconnects = 0;
class FakeResizeObserver {
  constructor(private cb: ResizeObserverCallback) {}
  observe() {
    report = (width) =>
      this.cb([{ contentRect: { width } } as ResizeObserverEntry], this as unknown as ResizeObserver);
  }
  unobserve() {}
  disconnect() {
    disconnects += 1;
  }
}

function Probe() {
  const ref = useRef<HTMLDivElement>(null);
  const bp = useGridBreakpoint(ref);
  return <div ref={ref} data-testid="probe" data-bp={bp} />;
}

afterEach(() => {
  vi.unstubAllGlobals();
  report = null;
  disconnects = 0;
});

describe("useGridBreakpoint", () => {
  it("is lg before a measurement and follows the container width (900 / 520 thresholds)", () => {
    vi.stubGlobal("ResizeObserver", FakeResizeObserver);
    const { unmount } = render(<Probe />);
    const probe = screen.getByTestId("probe");
    expect(probe).toHaveAttribute("data-bp", "lg");
    act(() => report!(899));
    expect(probe).toHaveAttribute("data-bp", "md");
    act(() => report!(519));
    expect(probe).toHaveAttribute("data-bp", "sm");
    act(() => report!(0)); // not laid out: ignored
    expect(probe).toHaveAttribute("data-bp", "sm");
    act(() => report!(900));
    expect(probe).toHaveAttribute("data-bp", "lg");
    unmount();
    expect(disconnects).toBe(1);
  });

  it("reads the width once when ResizeObserver is missing", () => {
    vi.stubGlobal("ResizeObserver", undefined);
    const spy = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({ width: 400 } as DOMRect);
    render(<Probe />);
    expect(screen.getByTestId("probe")).toHaveAttribute("data-bp", "sm");
    spy.mockRestore();
  });
});
