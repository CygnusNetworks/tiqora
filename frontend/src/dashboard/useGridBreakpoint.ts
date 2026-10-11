import { useLayoutEffect, useState, type RefObject } from "react";
import { breakpointForWidth } from "./layout/breakpoints";
import type { Breakpoint } from "./model";

/**
 * Breakpoint of the dashboard by the width of its own container (not the window), so the grid
 * also reflows when a side panel opens. "lg" until the first measurement; a zero width (element
 * not laid out yet) is ignored. Without `ResizeObserver` the width is read once.
 */
export function useGridBreakpoint(ref: RefObject<HTMLElement | null>): Breakpoint {
  const [bp, setBp] = useState<Breakpoint>("lg");
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const apply = (width: number) => {
      if (width > 0) setBp(breakpointForWidth(width));
    };
    apply(el.getBoundingClientRect().width);
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[entries.length - 1];
      if (entry) apply(entry.contentRect.width);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);
  return bp;
}
