import { describe, expect, it } from "vitest";
import { demoReport } from "@/demo/reportsMock";
import type { ReportDefinition, ReportParameter } from "@/lib/reportsApi";
import { filterFromSearch, searchFromFilter, validateReportViewSearch } from "./reportViewSearch";

const P = (p: Partial<ReportParameter> & Pick<ReportParameter, "key" | "field">): ReportParameter => ({
  op: "in",
  required: false,
  ...p,
});

const defWith = (parameters: ReportParameter[]): ReportDefinition => ({
  ...demoReport("builtin.volume")!.definition,
  parameters,
});

describe("params in the address", () => {
  it("keeps lists (numbers stay numbers) and booleans, wraps a lone value, drops junk", () => {
    const out = validateReportViewSearch({
      params: { ch: ["email", 3], one: "phone", n: 4, on: true, Bad: ["x"], empty: [], nested: [{ a: 1 }] },
    });
    expect(out.params).toEqual({ ch: ["email", 3], one: ["phone"], n: [4], on: true });
  });

  it("accepts the JSON text of the object", () => {
    expect(validateReportViewSearch({ params: '{"ch":["email"]}' }).params).toEqual({ ch: ["email"] });
    expect(validateReportViewSearch({ params: "kaputt" }).params).toBeUndefined();
  });

  it("applies only the definition's runtime parameters, in the shape their operator takes", () => {
    const def = defWith([
      P({ key: "ch", field: "channel", op: "eq" }),
      P({ key: "st", field: "state", op: "in" }),
      P({ key: "arch", field: "archived", op: "is_true" }),
      P({ key: "queues", field: "queue", op: "in" }),
    ]);
    const state = filterFromSearch(
      { params: { ch: ["email", "phone"], st: ["1", 2], arch: true, queues: [1], other: ["x"] } },
      def,
    );
    expect(state.params).toEqual({ ch: ["email"], st: ["1", 2], arch: true });
  });

  it("writes chosen values back into the address", () => {
    const search = searchFromFilter({
      preset: null, from: "", to: "", grain: null, choice: null, queues: [], params: { ch: ["email"], st: [] },
    });
    expect(search.params).toEqual({ ch: ["email"] });
    expect(searchFromFilter({ preset: null, from: "", to: "", grain: null, choice: null, queues: [] }).params).toBeUndefined();
  });
});
