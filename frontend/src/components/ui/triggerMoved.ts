/**
 * Whether the trigger has moved since the panel was positioned from `at`.
 *
 * Scroll events are dispatched on the next frame, so a page scroll that
 * happened *before* the menu opened (trackpad momentum, a scroll-into-view
 * right before the click) still reaches the freshly installed listener. The
 * panel is only stranded if the trigger actually moved; otherwise keep it.
 */
export function triggerMoved(el: HTMLElement | null, at: DOMRect | null): boolean {
  if (!el || !at) return true;
  const now = el.getBoundingClientRect();
  return Math.abs(now.top - at.top) >= 1 || Math.abs(now.left - at.left) >= 1;
}
