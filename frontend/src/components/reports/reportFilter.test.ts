import { describe, expect, it } from "vitest";
import { demoReport } from "@/demo/reportsMock";
import type { ReportDefinition, ReportParameter } from "@/lib/reportsApi";
import { EMPTY_FILTER, missingRequired, runtimeParameters, toRunIn } from "./reportFilter";

const P = (p: Partial<ReportParameter> & Pick<ReportParameter, "key" | "field">): ReportParameter => ({
  op: "in",
  required: false,
  ...p,
});

const defWith = (parameters: ReportParameter[]): ReportDefinition => ({
  ...demoReport("builtin.volume")!.definition,
  parameters,
});
const ch = P({ key: "ch", field: "channel" });

describe("runtime parameters in the run request", () => {
  it("carries chosen values next to the queue ids", () => {
    const body = toRunIn("7", { ...EMPTY_FILTER, queues: [1], params: { ch: ["email"], arch: true } }, defWith([
      ch,
      P({ key: "arch", field: "archived", op: "is_true" }),
    ]));
    expect(body.parameters).toEqual({ ch: ["email"], arch: true, queues: [1] });
  });

  it("drops empty, switched-off and unknown values", () => {
    const body = toRunIn("7", { ...EMPTY_FILTER, params: { ch: [], arch: false, nope: ["x"] } }, defWith([
      ch,
      P({ key: "arch", field: "archived", op: "is_true" }),
    ]));
    expect(body.parameters).toBeUndefined();
  });

  it("sends a required parameter's default when nothing was chosen", () => {
    const def = defWith([{ ...ch, required: true, default: ["phone"] }]);
    expect(toRunIn("7", EMPTY_FILTER, def).parameters).toEqual({ ch: ["phone"] });
    expect(missingRequired(def, {})).toEqual([]);
  });

  it("lists required parameters without a value, and ignores the queue parameter", () => {
    const def = defWith([{ ...ch, required: true }, P({ key: "queues", field: "queue", op: "in", required: true })]);
    expect(missingRequired(def, {}).map((p) => p.key)).toEqual(["ch"]);
    expect(missingRequired(def, { ch: ["email"] })).toEqual([]);
    expect(runtimeParameters(def).map((p) => p.key)).toEqual(["ch"]);
  });
});
