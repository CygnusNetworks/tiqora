import { describe, expect, it } from "vitest";
import { DF_KIND, dfToFieldValue, dynamicFieldToDef, fieldValueToDf } from "./dynamicFields";

describe("dynamic field adapter", () => {
  it("maps Znuny field types", () => {
    expect(
      dynamicFieldToDef({
        name: "Area",
        label: "Bereich",
        field_type: "Dropdown",
        required: true,
        possible_values: { net: "Netz" },
      }),
    ).toMatchObject({
      key: "Area",
      kind: "select",
      required: true,
      optionsKey: "DynamicField_Area",
      allowEmpty: true,
      options: [{ value: "net", label: "Netz" }],
    });
    expect(DF_KIND).toMatchObject({
      Text: "text",
      TextArea: "textarea",
      Multiselect: "multiselect",
      Checkbox: "boolean",
      Date: "date",
      DateTime: "datetime",
    });
    expect(
      dynamicFieldToDef({ name: "X", label: "X", field_type: "Unknown", required: false, possible_values: null }).kind,
    ).toBe("text");
  });

  it("checkbox and date round trips", () => {
    expect(dfToFieldValue("boolean", [1])).toBe(true);
    expect(dfToFieldValue("boolean", [])).toBe(false);
    expect(fieldValueToDf("boolean", false)).toEqual(["0"]);
    expect(fieldValueToDf("boolean", true)).toEqual(["1"]);
    expect(dfToFieldValue("date", ["2026-02-01T00:00:00"])).toBe("2026-02-01");
    expect(fieldValueToDf("date", "2026-02-01")).toEqual(["2026-02-01 00:00:00"]);
    expect(fieldValueToDf("text", "")).toEqual([]);
    expect(fieldValueToDf("multiselect", ["a", "b"])).toEqual(["a", "b"]);
    expect(dfToFieldValue("multiselect", ["a", "b"])).toEqual(["a", "b"]);
    expect(dfToFieldValue("text", ["hi"])).toBe("hi");
  });

  it("datetime is stored as UTC and shown in the agent's zone", () => {
    expect(fieldValueToDf("datetime", "2026-07-01T10:30", "Europe/Berlin")).toEqual(["2026-07-01 08:30:00"]);
    expect(dfToFieldValue("datetime", ["2026-07-01 08:30:00"], "Europe/Berlin")).toBe("2026-07-01T10:30");
    expect(dfToFieldValue("datetime", ["2026-01-15T08:30:00"], "Europe/Berlin")).toBe("2026-01-15T09:30");
    expect(fieldValueToDf("datetime", "", "Europe/Berlin")).toEqual([]);
  });
});
