import { describe, expect, it } from "vitest";
import opsFixture from "./opsByType.fixture.json";
import {
  OPS_BY_TYPE,
  conditionError,
  defaultOp,
  emptyValueFor,
  filterErrors,
  fromFilter,
  toFilter,
  type FilterFieldDef,
  type FilterFieldType,
} from "./filterModel";

const dateField: FilterFieldDef = {
  key: "created",
  label: "Erstellt",
  type: "datetime",
  ops: ["within_last", "between"],
  enumValues: [],
  optionsSource: null,
};

describe("filterModel", () => {
  it("mirrors backend OPS_BY_TYPE", () => {
    expect(Object.keys(OPS_BY_TYPE).sort()).toEqual(Object.keys(opsFixture).sort());
    for (const [type, ops] of Object.entries(opsFixture)) {
      expect([...OPS_BY_TYPE[type as FilterFieldType]].sort()).toEqual(ops);
    }
  });

  it("round-trips a two-level filter", () => {
    const f = {
      version: 1,
      where: {
        all: [
          { field: "queue_id", op: "in", value: [1, 2] },
          { any: [{ field: "title", op: "contains", value: "VPN" }, { field: "locked", op: "is_true" }] },
        ],
      },
    };
    const { root, editable } = fromFilter(f as never);
    expect(editable).toBe(true);
    expect(toFilter(root)).toEqual(f);
  });

  it("negated groups round-trip as not", () => {
    const f = { version: 1, where: { not: { any: [{ field: "queue_id", op: "eq", value: 3 }] } } };
    const { root } = fromFilter(f as never);
    expect(root.negate).toBe(true);
    expect(toFilter(root)).toEqual(f);
  });

  it("round-trips a negated nested group", () => {
    const f = {
      version: 1,
      where: { all: [{ not: { any: [{ field: "title", op: "eq", value: "a" }] } }, { field: "tn", op: "eq", value: "1" }] },
    };
    const { root, editable } = fromFilter(f as never);
    expect(editable).toBe(true);
    expect(root.children[0]).toMatchObject({ kind: "group", negate: true, mode: "any" });
    expect(toFilter(root)).toEqual(f);
  });

  it("shows a lone condition as a group of one", () => {
    const { root, editable } = fromFilter({ version: 1, where: { field: "tn", op: "eq", value: "1" } } as never);
    expect(editable).toBe(true);
    expect(toFilter(root)).toEqual({ version: 1, where: { all: [{ field: "tn", op: "eq", value: "1" }] } });
  });

  it("marks filters deeper than the UI as not editable", () => {
    const deep = { version: 1, where: { all: [{ any: [{ all: [{ field: "tn", op: "eq", value: "1" }] }] }] } };
    expect(fromFilter(deep as never).editable).toBe(false);
  });

  it("marks a negated single condition as not editable", () => {
    const f = { version: 1, where: { not: { field: "tn", op: "eq", value: "1" } } };
    expect(fromFilter(f as never).editable).toBe(false);
    const nested = { version: 1, where: { all: [{ not: { field: "tn", op: "eq", value: "1" } }] } };
    expect(fromFilter(nested as never).editable).toBe(false);
  });

  it("an empty root gives a filter without where", () => {
    expect(toFilter(fromFilter(null).root)).toEqual({ version: 1 });
    expect(toFilter(fromFilter({ version: 1 }).root)).toEqual({ version: 1 });
  });

  it("validates values per op", () => {
    expect(conditionError({ kind: "condition", id: "a", field: "created", op: "within_last", value: { amount: 0, unit: "day" } }, dateField)).toBe("filters.invalid.number");
    expect(conditionError({ kind: "condition", id: "a", field: "created", op: "between", value: { from: "2026-01-02", to: "" } }, dateField)).toBe("filters.invalid.date");
    expect(conditionError({ kind: "condition", id: "a", field: "created", op: "within_last", value: { amount: 3, unit: "day" } }, dateField)).toBeNull();
    expect(conditionError({ kind: "condition", id: "a", field: "created", op: "between", value: { from: "2026-01-02", to: "2026-02-01" } }, dateField)).toBeNull();
    expect(emptyValueFor("in", "id")).toEqual([]);
    expect(emptyValueFor("is_true", "bool")).toBeUndefined();
    expect(emptyValueFor("eq", "string")).toBe("");
  });

  it("flags empty lists, texts and unknown fields", () => {
    const str: FilterFieldDef = { key: "title", label: "Titel", type: "string", ops: ["eq", "in"], enumValues: [], optionsSource: null };
    expect(conditionError({ kind: "condition", id: "a", field: "title", op: "in", value: [] }, str)).toBe("filters.invalid.list");
    expect(conditionError({ kind: "condition", id: "a", field: "title", op: "eq", value: "" }, str)).toBe("filters.invalid.required");
    expect(conditionError({ kind: "condition", id: "a", field: "title", op: "eq", value: "x" }, str)).toBeNull();
    expect(conditionError({ kind: "condition", id: "a", field: "nope", op: "eq", value: "x" }, undefined)).toBe("filters.invalid.required");
    const id: FilterFieldDef = { key: "queue_id", label: "Queue", type: "id", ops: ["in"], enumValues: [], optionsSource: "queue" };
    expect(conditionError({ kind: "condition", id: "a", field: "queue_id", op: "in", value: [1, "x"] }, id)).toBe("filters.invalid.number");
  });

  it("collects errors by condition id", () => {
    const { root } = fromFilter({ version: 1, where: { all: [{ field: "created", op: "between", value: { from: "", to: "" } }] } } as never);
    const errs = filterErrors(root, [dateField]);
    expect([...errs.values()]).toEqual(["filters.invalid.date"]);
    expect([...errs.keys()]).toEqual([root.children[0].id]);
  });

  it("picks eq, in, is_true, before, contains in that order, else the first operator", () => {
    expect(defaultOp(["contains", "eq", "starts_with"])).toBe("eq");
    expect(defaultOp(["after", "before", "between"])).toBe("before");
    expect(defaultOp(["is_false", "is_true"])).toBe("is_true");
    expect(defaultOp(["in", "not_in"])).toBe("in");
    expect(defaultOp(["lte"])).toBe("lte");
  });
});
