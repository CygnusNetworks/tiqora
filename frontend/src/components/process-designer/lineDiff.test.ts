import { describe, it, expect } from "vitest";
import { diffLines } from "./lineDiff";

describe("diffLines", () => {
  it("marks unchanged text as same", () => {
    expect(diffLines("a\nb\n", "a\nb\n").map((r) => r.kind)).toEqual(["same", "same"]);
  });

  it("pairs a replaced line as changed and keeps the surrounding lines aligned", () => {
    const rows = diffLines("a\nOLD\nc\n", "a\nNEW\nc\n");
    expect(rows).toEqual([
      { kind: "same", left: "a", right: "a" },
      { kind: "changed", left: "OLD", right: "NEW" },
      { kind: "same", left: "c", right: "c" },
    ]);
  });

  it("marks inserted and deleted lines on one side only", () => {
    const rows = diffLines("a\nb\nc\n", "a\nc\nd\n");
    expect(rows.map((r) => r.kind)).toEqual(["same", "removed", "same", "added"]);
    expect(rows[1]).toEqual({ kind: "removed", left: "b", right: null });
    expect(rows[3]).toEqual({ kind: "added", left: null, right: "d" });
  });

  it("handles an empty side (first revision against nothing)", () => {
    expect(diffLines("", "x\ny\n").map((r) => r.kind)).toEqual(["added", "added"]);
    expect(diffLines("x\n", "").map((r) => r.kind)).toEqual(["removed"]);
    expect(diffLines("", "")).toEqual([]);
  });
});
