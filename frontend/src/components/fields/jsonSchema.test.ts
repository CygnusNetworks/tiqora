import { describe, it, expect, vi } from "vitest";
import type { TFunction } from "i18next";
import { schemaDefaults, schemaToFieldDefs, type JsonSchema } from "./jsonSchema";

const schema: JsonSchema = {
  type: "object",
  title: "Demo",
  required: [],
  properties: {
    enabled: { type: "boolean", default: false, title: "Enabled" },
    limit: { type: "integer", minimum: 1, maximum: 10, default: 5, title: "Limit" },
    mode: { enum: ["a", "b"], type: "string", default: "a", title: "Mode" },
    level: { $ref: "#/$defs/Level", default: "low" },
    hosts: { type: "array", items: { type: "string" }, title: "Hosts" },
    token: { anyOf: [{ type: "string", format: "password", writeOnly: true }, { type: "null" }], default: null },
    note: { type: "string", "x-widget": "textarea", default: "" },
    blob: { type: "object", title: "Blob" },
  },
  $defs: { Level: { enum: ["low", "high"], type: "string", title: "Level" } },
};

const t = ((k: string, o?: { defaultValue?: string }) => o?.defaultValue ?? k) as TFunction;

describe("schemaToFieldDefs", () => {
  it("maps the supported subset and lists the rest as unsupported", () => {
    const { fields, unsupported } = schemaToFieldDefs(schema, { t, i18nPrefix: "p", secretsSet: ["token"] });
    expect(Object.fromEntries(fields.map((f) => [f.key, f.kind]))).toEqual({
      enabled: "boolean",
      limit: "number",
      mode: "select",
      level: "select",
      hosts: "tags",
      token: "secret",
      note: "textarea",
    });
    expect(fields.find((f) => f.key === "limit")).toMatchObject({ min: 1, max: 10 });
    expect(fields.find((f) => f.key === "token")).toMatchObject({ secretSet: true });
    expect(fields.find((f) => f.key === "level")?.options?.map((o) => o.value)).toEqual(["low", "high"]);
    expect(unsupported).toEqual(["blob"]);
  });

  it("labels come from i18n keys with the schema title as fallback", () => {
    const spy = vi.fn((k: string, o?: { defaultValue?: string }) => o?.defaultValue ?? k);
    const { fields } = schemaToFieldDefs(schema, { t: spy as unknown as TFunction, i18nPrefix: "p" });
    expect(spy).toHaveBeenCalledWith("p.fields.limit.label", expect.objectContaining({ defaultValue: "Limit" }));
    expect(fields.find((f) => f.key === "limit")?.label).toBe("Limit");
    expect(fields.find((f) => f.key === "mode")?.options?.[0].label).toBe("a");
    const withPrefixOnly = schemaToFieldDefs(schema, { t, i18nPrefix: "p" }).fields;
    expect(withPrefixOnly.find((f) => f.key === "token")?.secretSet).toBeFalsy();
  });

  it("maps anyOf-of-const, optional enums, date formats and maxLength", () => {
    const s: JsonSchema = {
      type: "object",
      properties: {
        pick: { anyOf: [{ const: "x", title: "X" }, { const: "y" }] },
        opt: { anyOf: [{ type: "integer", minimum: 0 }, { type: "null" }], default: null },
        day: { type: "string", format: "date" },
        at: { type: "string", format: "date-time" },
        name: { type: "string", maxLength: 12 },
        many: { type: "array", items: { enum: ["u", "v"] } },
      },
    };
    const { fields, unsupported } = schemaToFieldDefs(s, { t });
    expect(unsupported).toEqual([]);
    const kinds = Object.fromEntries(fields.map((f) => [f.key, f.kind]));
    expect(kinds).toEqual({
      pick: "select",
      opt: "number",
      day: "date",
      at: "datetime",
      name: "text",
      many: "multiselect",
    });
    expect(fields.find((f) => f.key === "name")?.maxLength).toBe(12);
    expect(fields.find((f) => f.key === "pick")?.options?.map((o) => o.value)).toEqual(["x", "y"]);
    expect(fields.find((f) => f.key === "opt")?.min).toBe(0);
  });
});

describe("schemaDefaults", () => {
  it("reads defaults and nulls secrets", () => {
    expect(schemaDefaults(schema)).toMatchObject({ limit: 5, mode: "a", level: "low", enabled: false, token: null });
  });
});
