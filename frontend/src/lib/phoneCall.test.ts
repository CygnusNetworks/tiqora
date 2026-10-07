import { afterEach, describe, expect, it } from "vitest";
import {
  callbackPresetDate,
  callbackState,
  clearPhoneDraft,
  defaultStateFor,
  dialHref,
  elapsedToMinutes,
  formatElapsed,
  loadPhoneDraft,
  pendingIso,
  savePhoneDraft,
  toLocalInputValue,
} from "./phoneCall";

const STATES = [
  { id: 1, name: "new", type_name: "new" },
  { id: 4, name: "open", type_name: "open" },
  { id: 3, name: "closed unsuccessful", type_name: "closed" },
  { id: 2, name: "closed successful", type_name: "closed" },
  { id: 6, name: "pending reminder", type_name: "pending reminder" },
];

describe("phoneCall helpers", () => {
  afterEach(() => window.localStorage.clear());

  it("formats the timer as mm:ss and h:mm:ss", () => {
    expect(formatElapsed(0)).toBe("00:00");
    expect(formatElapsed(65)).toBe("01:05");
    expect(formatElapsed(3725)).toBe("1:02:05");
  });

  it("rounds a call's duration up to whole minutes", () => {
    expect(elapsedToMinutes(0)).toBe(0);
    expect(elapsedToMinutes(1)).toBe(1);
    expect(elapsedToMinutes(60)).toBe(1);
    expect(elapsedToMinutes(61)).toBe(2);
  });

  it("computes callback presets relative to now", () => {
    const now = new Date(2026, 8, 29, 14, 20, 30);
    expect(callbackPresetDate("inOneHour", now)).toEqual(new Date(2026, 8, 29, 15, 20, 0));
    expect(callbackPresetDate("today16", now)).toEqual(new Date(2026, 8, 29, 16, 0, 0));
    expect(callbackPresetDate("tomorrow9", now)).toEqual(new Date(2026, 8, 30, 9, 0, 0));
    // After 16:00 "today 16:00" is gone.
    expect(callbackPresetDate("today16", new Date(2026, 8, 29, 16, 5))).toBeNull();
  });

  it("anchors callback presets to the wall clock of the given zone", () => {
    // 21:00 UTC: 23:00 in Berlin, 17:00 in New York.
    const now = new Date("2026-09-29T21:00:00Z");
    expect(callbackPresetDate("today16", now, "Europe/Berlin")).toBeNull();
    expect(callbackPresetDate("today16", now, "America/New_York")).toBeNull();
    expect(callbackPresetDate("tomorrow9", now, "Europe/Berlin")?.toISOString()).toBe(
      "2026-09-30T07:00:00.000Z",
    );
    expect(callbackPresetDate("tomorrow9", now, "America/New_York")?.toISOString()).toBe(
      "2026-09-30T13:00:00.000Z",
    );
    expect(toLocalInputValue(now, "Europe/Berlin")).toBe("2026-09-29T23:00");
    expect(pendingIso("2026-09-30T09:00", "Europe/Berlin")).toBe("2026-09-30T07:00:00.000Z");
    expect(pendingIso("", "Europe/Berlin")).toBeNull();
  });

  it("picks the Znuny default state per direction and the callback state", () => {
    expect(defaultStateFor("outbound", STATES)?.id).toBe(2);
    expect(defaultStateFor("inbound", STATES)?.id).toBe(4);
    expect(callbackState(STATES)?.id).toBe(6);
    expect(defaultStateFor("outbound", STATES.filter((s) => s.id !== 2))?.id).toBe(3);
  });

  it("builds tel:/sip: links from formatted numbers", () => {
    expect(dialHref("+49 (228) 555-0101")).toBe("tel:+492285550101");
    expect(dialHref("0228 / 5550101", "sip")).toBe("sip:02285550101");
  });

  it("keeps a per-ticket draft in localStorage", () => {
    expect(loadPhoneDraft(7)).toBeNull();
    savePhoneDraft(7, { direction: "outbound", subject: "S", body: "B", elapsed: 42 });
    expect(loadPhoneDraft(7)).toEqual({ direction: "outbound", subject: "S", body: "B", elapsed: 42 });
    expect(loadPhoneDraft(8)).toBeNull();
    clearPhoneDraft(7);
    expect(loadPhoneDraft(7)).toBeNull();
  });
});

describe("CTI handover", () => {
  it("timerFromCall counts from the answered time or freezes at hangup", async () => {
    const { timerFromCall } = await import("./phoneCall");
    expect(timerFromCall(null, null)).toBeNull();
    expect(timerFromCall(1_000, null, 91_500)).toEqual({ initialSeconds: 90, autoStart: true });
    expect(timerFromCall(1_000, 61_000, 999_000)).toEqual({ initialSeconds: 60, autoStart: false });
  });

  it("notifies subscribers of a phone-call request", async () => {
    const { consumePhoneCallRequest, peekPhoneCallRequest, requestPhoneCall, subscribePhoneCallRequests } =
      await import("./phoneCall");
    const seen: number[] = [];
    const off = subscribePhoneCallRequests((id) => seen.push(id));
    requestPhoneCall(42, { direction: "inbound", startedAt: 5 });
    off();
    requestPhoneCall(43, { direction: "inbound" });
    expect(seen).toEqual([42]);
    expect(peekPhoneCallRequest(42)?.startedAt).toBe(5);
    consumePhoneCallRequest(42);
    consumePhoneCallRequest(43);
  });
});
