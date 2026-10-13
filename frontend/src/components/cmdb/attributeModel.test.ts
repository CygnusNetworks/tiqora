import { describe, expect, it } from "vitest";
import type { TFunction } from "i18next";
import type { AttributeDef } from "@/lib/cmdbApi";
import {
  addEntry,
  emptyValues,
  fromFieldValue,
  pruneEmpty,
  removeEntry,
  setValue,
  toFieldDef,
  toFieldValue,
  unknownKeys,
  validateValues,
} from "./attributeModel";

const t = ((k: string) => k) as unknown as TFunction;

const NIC: AttributeDef = {
  key: "NIC",
  name: "Netzwerkkarte",
  type: "text",
  count_min: 0,
  count_max: 3,
  count_default: 1,
  required: false,
  searchable: false,
  portal: false,
  default: null,
  help: null,
  options: {} as never,
  sub: [{ key: "IPAddress", name: "IP-Adresse", type: "ip", count_min: 0, count_max: 4, count_default: 1, sub: [] } as unknown as AttributeDef],
};

const def = (over: Partial<AttributeDef>): AttributeDef =>
  ({ key: "X", name: "X", type: "text", count_min: 0, count_max: 1, count_default: 1, required: false, options: {}, sub: [], ...over }) as AttributeDef;

describe("attributeModel", () => {
  it("creates count_default entries recursively", () => {
    expect(emptyValues([NIC])).toEqual({ NIC: [{ v: null, s: { IPAddress: [{ v: null }] } }] });
  });

  it("adds and removes entries within the count bounds", () => {
    let v = emptyValues([NIC]);
    v = addEntry(v, [{ key: "NIC", index: 0 }], NIC);
    expect(v.NIC).toHaveLength(2);
    v = addEntry(addEntry(v, [{ key: "NIC", index: 1 }], NIC), [{ key: "NIC", index: 2 }], NIC);
    expect(v.NIC).toHaveLength(3); // count_max is 3: the fourth add is a no-op
    v = removeEntry(v, [{ key: "NIC", index: 1 }]);
    expect(v.NIC).toHaveLength(2);
  });

  it("adds and sets nested entries by path", () => {
    let v = emptyValues([NIC]);
    v = addEntry(v, [{ key: "NIC", index: 0 }, { key: "IPAddress", index: 0 }], NIC.sub![0]);
    expect(v.NIC[0].s?.IPAddress).toHaveLength(2);
    v = setValue(v, [{ key: "NIC", index: 0 }, { key: "IPAddress", index: 1 }], "192.0.2.1");
    expect(v.NIC[0].s?.IPAddress[1].v).toBe("192.0.2.1");
    expect(v.NIC[0].s?.IPAddress[0].v).toBeNull();
  });

  it("prunes empty entries like the backend", () => {
    expect(pruneEmpty([NIC], emptyValues([NIC]))).toEqual({});
    const v = setValue(emptyValues([NIC]), [{ key: "NIC", index: 0 }], "eth0");
    expect(pruneEmpty([NIC], v)).toEqual({ NIC: [{ v: "eth0", s: {} }] });
  });

  it("keeps the empty entry of a required attribute and reports it (F18)", () => {
    const req = def({ key: "Vendor", required: true });
    const v = emptyValues([req]);
    expect(pruneEmpty([req], v)).toEqual({ Vendor: [{ v: null }] });
    expect(validateValues([req], v)).toEqual({ "Vendor[1]": "cmdb.validation.required" });
  });

  it("reports unknown top-level keys", () => {
    expect(unknownKeys([NIC], { NIC: [], CPU: [{ v: 4 }] })).toEqual(["CPU"]);
  });

  it("validates ip, required and count like the backend codes", () => {
    const errs = validateValues([NIC], { NIC: [{ v: "eth0", s: { IPAddress: [{ v: "999.1.1.1" }] } }] });
    expect(errs["NIC[1].IPAddress[1]"]).toBe("cmdb.validation.bad_format");
    expect(validateValues([NIC], { NIC: [{ v: "eth0", s: { IPAddress: [{ v: "192.0.2.10" }, { v: "2001:db8::1" }] } }] })).toEqual({});
    const four = [{ v: "a" }, { v: "b" }, { v: "c" }, { v: "d" }];
    expect(validateValues([NIC], { NIC: four })["NIC"]).toBe("cmdb.validation.too_many");
    const min = def({ key: "Disk", count_min: 2, count_max: 3 });
    expect(validateValues([min], { Disk: [{ v: "a" }] })["Disk"]).toBe("cmdb.validation.too_few");
  });

  it("validates integer range, length, email, url, date", () => {
    const num = def({ key: "N", type: "integer", options: { min: "1", max: "10" } as never });
    expect(validateValues([num], { N: [{ v: 11 }] })["N[1]"]).toBe("cmdb.validation.out_of_range");
    expect(validateValues([num], { N: [{ v: "x1" }] })["N[1]"]).toBe("cmdb.validation.bad_type");
    expect(validateValues([num], { N: [{ v: 5 }] })).toEqual({});
    const short = def({ key: "S", options: { max_length: 3 } as never });
    expect(validateValues([short], { S: [{ v: "abcd" }] })["S[1]"]).toBe("cmdb.validation.too_long");
    expect(validateValues([def({ key: "E", type: "email" })], { E: [{ v: "nobody" }] })["E[1]"]).toBe("cmdb.validation.bad_format");
    expect(validateValues([def({ key: "U", type: "url" })], { U: [{ v: "javascript:alert(1)" }] })["U[1]"]).toBe("cmdb.validation.bad_format");
    expect(validateValues([def({ key: "D", type: "date" })], { D: [{ v: "2026-02-30" }] })["D[1]"]).toBe("cmdb.validation.bad_format");
  });

  it("maps attribute types onto field kinds", () => {
    expect(toFieldDef({ ...NIC, type: "ci_ref", options: { class_keys: ["location"] } } as AttributeDef, t, {}).kind).toBe("ci_ref");
    expect(toFieldDef({ ...NIC, type: "ci_ref", options: { class_keys: ["location"] } } as AttributeDef, t, {}).lookupClassKeys).toEqual(["location"]);
    expect(toFieldDef({ ...NIC, type: "customer_company" } as AttributeDef, t, {}).kind).toBe("company");
    expect(toFieldDef({ ...NIC, type: "integer" } as AttributeDef, t, {}).step).toBe(1);
    const sel = toFieldDef(def({ type: "queue" }), t, { queue: [{ value: "3", label: "Raw" }] });
    expect(sel.kind).toBe("select");
    expect(sel.options).toEqual([{ value: "3", label: "Raw" }]);
    const cat = toFieldDef(def({ type: "catalog", options: { catalog_class: "Vendor" } as never }), t, { catalog: { Vendor: [{ value: "7", label: "Example" }] } });
    expect(cat.options).toEqual([{ value: "7", label: "Example" }]);
  });

  it("converts values to and from field values, datetime through the zone", () => {
    expect(toFieldValue(def({ type: "ci_ref" }), 42)).toBe("42");
    expect(fromFieldValue(def({ type: "ci_ref" }), "42")).toBe(42);
    expect(fromFieldValue(def({ type: "integer" }), 5)).toBe(5);
    expect(fromFieldValue(def({ type: "text" }), "")).toBeNull();
    const dt = def({ type: "datetime" });
    expect(toFieldValue(dt, "2026-07-01T10:00:00", "Europe/Berlin")).toBe("2026-07-01T12:00");
    expect(fromFieldValue(dt, "2026-07-01T12:00", "Europe/Berlin")).toBe("2026-07-01T10:00:00");
  });
});
