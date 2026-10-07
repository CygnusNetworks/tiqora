import { useCallback, useEffect, useState } from "react";

/**
 * Stopwatch for a phone call: starts running (or paused) at *initialSeconds*,
 * ticks once a second, can be paused and resumed, or set (`reset`) when the
 * PBX reports the call's answer or end. `elapsed` is whole seconds.
 */
export function useCallTimer({
  initialSeconds = 0,
  autoStart = true,
}: { initialSeconds?: number; autoStart?: boolean } = {}) {
  // Seconds accumulated before the current run, and when that run started.
  const [base, setBase] = useState(initialSeconds);
  const [startedAt, setStartedAt] = useState<number | null>(() => (autoStart ? Date.now() : null));
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (startedAt === null) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [startedAt]);

  const running = startedAt !== null;
  const elapsed = base + (startedAt !== null ? Math.max(0, Math.floor((now - startedAt) / 1000)) : 0);

  const pause = useCallback(() => {
    if (startedAt === null) return;
    const stamp = Date.now();
    setBase((b) => b + Math.max(0, Math.floor((stamp - startedAt) / 1000)));
    setStartedAt(null);
    setNow(stamp);
  }, [startedAt]);

  const resume = useCallback(() => {
    if (startedAt !== null) return;
    const stamp = Date.now();
    setStartedAt(stamp);
    setNow(stamp);
  }, [startedAt]);

  const reset = useCallback((seconds: number, run: boolean) => {
    const stamp = Date.now();
    setBase(Math.max(0, seconds));
    setStartedAt(run ? stamp : null);
    setNow(stamp);
  }, []);

  return { elapsed, running, pause, resume, reset };
}
