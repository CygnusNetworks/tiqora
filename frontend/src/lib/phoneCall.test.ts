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
  savePhoneDraft,
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
