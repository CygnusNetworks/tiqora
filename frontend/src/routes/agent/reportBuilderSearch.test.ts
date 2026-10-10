import { describe, expect, it } from "vitest";
import { validateReportBuilderSearch } from "./reportBuilderSearch";

describe("validateReportBuilderSearch", () => {
  it("keeps a report key, reads a bare number as a saved key", () => {
    expect(validateReportBuilderSearch({ from: "builtin.volume" })).toEqual({ from: "builtin.volume" });
    expect(validateReportBuilderSearch({ from: 41 })).toEqual({ from: "41" });
    expect(validateReportBuilderSearch({ from: "41" })).toEqual({ from: "41" });
  });
  it("drops anything that is not a report key", () => {
    for (const from of ["../x", "a b", "", 1.5, null, { k: 1 }, "x".repeat(121)]) {
      expect(validateReportBuilderSearch({ from }), String(from)).toEqual({ from: undefined });
    }
    expect(validateReportBuilderSearch({})).toEqual({ from: undefined });
  });
});
