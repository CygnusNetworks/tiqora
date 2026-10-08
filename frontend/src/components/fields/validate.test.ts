import { describe, it, expect } from "vitest";
import type { TFunction } from "i18next";
import { isEmptyFieldValue, validateFieldValues } from "./validate";

const t = ((k: string) => k) as unknown as TFunction;

describe("validateFieldValues", () => {
  it("flags empty required values, numbers out of range, too long text", () => {
    expect(validateFieldValues([{ key: "a", kind: "text", label: "A", required: true }], { a: " " }, t)).toEqual({
      a: "fields.required",
    });
    expect(validateFieldValues([{ key: "n", kind: "number", label: "N", min: 1, max: 8 }], { n: 9 }, t)).toEqual({
      n: "fields.invalidNumber",
    });
    expect(validateFieldValues([{ key: "b", kind: "boolean", label: "B", required: true }], { b: false }, t)).toEqual({
      b: "fields.required",
    });
    expect(validateFieldValues([{ key: "s", kind: "text", label: "S", maxLength: 3 }], { s: "abcd" }, t)).toEqual({
      s: "fields.tooLong",
    });
  });

  it("accepts valid and optional-empty values", () => {
    const fields = [
      { key: "a", kind: "text" as const, label: "A", required: true },
      { key: "n", kind: "number" as const, label: "N", min: 1, max: 8 },
      { key: "b", kind: "boolean" as const, label: "B" },
      { key: "m", kind: "multiselect" as const, label: "M", required: true },
    ];
    expect(validateFieldValues(fields, { a: "x", n: null, b: false, m: ["q"] }, t)).toEqual({});
    expect(validateFieldValues(fields, { a: "x", n: 1, m: [] }, t)).toEqual({ m: "fields.required" });
  });

  it("rejects non-finite numbers and skips read-only fields", () => {
    expect(validateFieldValues([{ key: "n", kind: "number", label: "N" }], { n: Number.NaN }, t)).toEqual({
      n: "fields.invalidNumber",
    });
    expect(validateFieldValues([{ key: "a", kind: "text", label: "A", required: true, readOnly: true }], {}, t)).toEqual({});
  });
});

describe("isEmptyFieldValue", () => {
  it("treats blank strings, empty arrays, null and undefined as empty", () => {
    expect(isEmptyFieldValue(undefined)).toBe(true);
    expect(isEmptyFieldValue(null)).toBe(true);
    expect(isEmptyFieldValue("  ")).toBe(true);
    expect(isEmptyFieldValue([])).toBe(true);
    expect(isEmptyFieldValue(0)).toBe(false);
    expect(isEmptyFieldValue(false)).toBe(false);
    expect(isEmptyFieldValue(["a"])).toBe(false);
  });
});
