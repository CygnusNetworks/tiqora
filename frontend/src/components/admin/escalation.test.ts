import { describe, it, expect } from "vitest";
import { activeEscalationStages, humanizeMinutes, toMinutes } from "./escalation";

const de = { day: "T", hour: "Std", minute: "Min" };

describe("humanizeMinutes", () => {
  it("splits minutes into days, hours and minutes, skipping zero parts", () => {
    expect(humanizeMinutes(150, de)).toBe("2 Std 30 Min");
    expect(humanizeMinutes(60, de)).toBe("1 Std");
    expect(humanizeMinutes(45, de)).toBe("45 Min");
    expect(humanizeMinutes(1440, de)).toBe("1 T");
    expect(humanizeMinutes(1440 + 61, de)).toBe("1 T 1 Std 1 Min");
  });

  it("is empty for 0, negatives and non-numbers", () => {
    expect(humanizeMinutes(0, de)).toBe("");
    expect(humanizeMinutes(-5, de)).toBe("");
    expect(humanizeMinutes(Number.NaN, de)).toBe("");
  });
});

describe("toMinutes / activeEscalationStages", () => {
  it("treats empty, null and junk as off", () => {
    expect(toMinutes("")).toBe(0);
    expect(toMinutes(null)).toBe(0);
    expect(toMinutes("abc")).toBe(0);
    expect(toMinutes("90")).toBe(90);
  });

  it("counts stages with a time set", () => {
    expect(activeEscalationStages({})).toBe(0);
    expect(
      activeEscalationStages({ first_response_time: 60, update_time: "", solution_time: 480 }),
    ).toBe(2);
  });
});
