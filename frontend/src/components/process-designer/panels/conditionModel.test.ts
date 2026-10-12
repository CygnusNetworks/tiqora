import { describe, it, expect } from "vitest";
import {
  buildCondition,
  buildTransitionConfig,
  duplicateFields,
  hasBlockingProblem,
  isUnknownModule,
  readCondition,
  regexpProblem,
  removeGroup,
} from "./conditionModel";

// Shapes as Znuny's own sample processes and AdminProcessManagementTransition store them.
const sample = {
  ConditionLinking: "or",
  Condition: {
    "1": {
      Type: "and",
      Fields: {
        Queue: { Type: "String", Match: "Raw" },
        DynamicField_Priorisiert: { Type: "Regexp", Match: "^ja$" },
      },
    },
    "2": { Type: "xor", Fields: { State: { Type: "Equal", Match: "open" } } },
  },
};

describe("conditionModel", () => {
  it("round-trips the Znuny sample condition unchanged", () => {
    const model = readCondition(sample);
    expect(model.linking).toBe("or");
    expect(model.groups.map((g) => g.linking)).toEqual(["and", "xor"]);
    expect(model.groups[0].rows).toEqual([
      { field: "Queue", type: "String", match: "Raw" },
      { field: "DynamicField_Priorisiert", type: "Regexp", match: "^ja$" },
    ]);
    expect(buildTransitionConfig({}, model)).toEqual(sample);
  });

  it("defaults to and and an empty condition; keeps foreign keys", () => {
    expect(readCondition({})).toEqual({ linking: "and", groups: [] });
    const out = buildTransitionConfig({ Extra: "x" }, readCondition({}));
    expect(out).toEqual({ Extra: "x", Condition: {}, ConditionLinking: "and" });
  });

  it("renumbers the group keys consecutively after removing a group", () => {
    const three = readCondition({
      Condition: {
        "1": { Type: "and", Fields: { A: { Type: "String", Match: "a" } } },
        "2": { Type: "and", Fields: { B: { Type: "String", Match: "b" } } },
        "4": { Type: "or", Fields: { C: { Type: "String", Match: "c" } } },
      },
    });
    const built = buildCondition(removeGroup(three, 0)) as Record<string, { Fields: Record<string, unknown> }>;
    expect(Object.keys(built)).toEqual(["1", "2"]);
    expect(Object.keys(built["1"].Fields)).toEqual(["B"]);
    expect(Object.keys(built["2"].Fields)).toEqual(["C"]);
  });

  it("keeps a Module with an unknown path and flags it", () => {
    const cfg = { Condition: { "1": { Type: "and", Fields: { Queue: { Type: "Module", Match: "Kernel::Foo::Bar" } } } } };
    const model = readCondition(cfg);
    expect(isUnknownModule(model.groups[0].rows[0], ["Kernel::System::ProcessManagement::TransitionValidation::ValidateDemo"])).toBe(true);
    expect(isUnknownModule(model.groups[0].rows[0], ["Kernel::Foo::Bar"])).toBe(false);
    const built = buildCondition(model) as Record<string, { Fields: Record<string, { Match: string }> }>;
    expect(built["1"].Fields.Queue.Match).toBe("Kernel::Foo::Bar");
    expect(hasBlockingProblem(model)).toBe(false);
  });

  it("keeps a non-scalar Match verbatim", () => {
    const cfg = { Condition: { "1": { Type: "and", Fields: { State: { Type: "Equal", Match: ["open", "new"] } } } } };
    const model = readCondition(cfg);
    expect(model.groups[0].rows[0].matchRaw).toEqual(["open", "new"]);
    expect(buildTransitionConfig({}, model).Condition).toEqual(cfg.Condition);
  });

  it("detects broken regexps, duplicate and empty fields", () => {
    expect(regexpProblem("")).toBe("empty");
    expect(regexpProblem("(")).toBe("invalid");
    expect(regexpProblem("^ja$")).toBeNull();
    const dup = { linking: "and" as const, rows: [
      { field: "Queue", type: "String", match: "a" },
      { field: "Queue", type: "String", match: "b" },
    ] };
    expect(duplicateFields(dup)).toEqual(["Queue"]);
    expect(hasBlockingProblem({ linking: "and", groups: [dup] })).toBe(true);
    expect(hasBlockingProblem({ linking: "and", groups: [{ linking: "and", rows: [{ field: "Q", type: "Regexp", match: "(" }] }] })).toBe(true);
    expect(hasBlockingProblem({ linking: "and", groups: [{ linking: "and", rows: [{ field: "", type: "String", match: "" }] }] })).toBe(true);
  });
});
