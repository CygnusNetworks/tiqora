import { describe, expect, it } from "vitest";
import type { DaemonServiceOut } from "@/lib/api";
import { statusColor } from "./daemonStatus";

const NOW = Date.parse("2026-10-06T12:00:00Z");

function svc(overrides: Partial<DaemonServiceOut> = {}): DaemonServiceOut {
  return {
    slug: "outbox",
    enabled: true,
    toggleable: true,
    schedule: "interval",
    interval_seconds: 5,
    interval_overridden: false,
    tick_timeout_seconds: 0,
    daily_at: null,
    last_run_at: null,
    last_ok_at: null,
    last_error: null,
    last_result: null,
    ...overrides,
  };
}

const agoIso = (seconds: number) => new Date(NOW - seconds * 1000).toISOString();

describe("statusColor", () => {
  it("is amber once an interval service is three intervals overdue", () => {
    expect(statusColor(svc({ last_ok_at: agoIso(14) }), NOW)).toBe("green");
    expect(statusColor(svc({ last_ok_at: agoIso(25) }), NOW)).toBe("amber");
  });

  it("counts a long-poll tick timeout into the cadence (telegram: 5s + 20s)", () => {
    const telegram = svc({ slug: "telegram_poller", tick_timeout_seconds: 20 });
    expect(statusColor({ ...telegram, last_ok_at: agoIso(25) }, NOW)).toBe("green");
    expect(statusColor({ ...telegram, last_ok_at: agoIso(74) }, NOW)).toBe("green");
    expect(statusColor({ ...telegram, last_ok_at: agoIso(76) }, NOW)).toBe("amber");
  });
});
