import { afterEach, describe, expect, it, vi } from "vitest";
import { setDisplayTimeZone } from "@/lib/timeZone";
import { defaultPendingDate, pendingTimeFor, todayIso } from "./replyNextState";

describe("reply next-state dates", () => {
  afterEach(() => {
    setDisplayTimeZone(null);
    vi.useRealTimers();
  });

  it("puts the reminder at 08:00 in the agent's zone", () => {
    setDisplayTimeZone("America/New_York");
    expect(pendingTimeFor("2026-07-01")).toBe("2026-07-01T12:00:00.000Z");
    setDisplayTimeZone("Europe/Berlin");
    expect(pendingTimeFor("2026-07-01")).toBe("2026-07-01T06:00:00.000Z");
    expect(pendingTimeFor("2026-12-01")).toBe("2026-12-01T07:00:00.000Z");
  });

  it("counts today and the default reminder day in the agent's zone", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    // 23:30 UTC on 31 March: already 1 April in Tokyo.
    vi.setSystemTime(new Date("2026-03-31T23:30:00Z"));
    setDisplayTimeZone("Asia/Tokyo");
    expect(todayIso()).toBe("2026-04-01");
    expect(defaultPendingDate()).toBe("2026-04-04");
    setDisplayTimeZone("America/Los_Angeles");
    expect(todayIso()).toBe("2026-03-31");
  });
});
