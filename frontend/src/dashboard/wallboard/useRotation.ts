import { useCallback, useEffect, useState } from "react";

function isHidden(): boolean {
  return typeof document !== "undefined" && document.hidden === true;
}

/**
 * Which of `count` dashboards is on screen. Advances every `seconds`, wraps around, stands still
 * while the page is hidden (or `opts.paused`) and starts no timer for fewer than two dashboards.
 * A manual `next`/`prev` restarts the countdown.
 */
export function useRotation(
  count: number,
  seconds: number,
  opts: { paused?: boolean } = {},
): { index: number; next: () => void; prev: () => void } {
  const paused = opts.paused === true;
  const [raw, setRaw] = useState(0);
  const [hidden, setHidden] = useState(isHidden);
  const index = count > 0 ? ((raw % count) + count) % count : 0;

  useEffect(() => {
    const onVisibility = () => setHidden(isHidden());
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  useEffect(() => {
    if (count < 2 || paused || hidden) return;
    const timer = setTimeout(() => setRaw(index + 1), seconds * 1000);
    return () => clearTimeout(timer);
  }, [count, seconds, paused, hidden, index]);

  const next = useCallback(() => setRaw(count > 0 ? (index + 1) % count : 0), [count, index]);
  const prev = useCallback(() => setRaw(count > 0 ? (index - 1 + count) % count : 0), [count, index]);
  return { index, next, prev };
}
