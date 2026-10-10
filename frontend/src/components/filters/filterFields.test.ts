import { describe, expect, it } from "vitest";
import type { TFunction } from "i18next";
import { fieldsFromSource, fieldsFromTicketCatalog } from "./filterFields";

const t = ((k: string) => k) as unknown as TFunction;

const dim = (over: Record<string, unknown>) => ({
  key: "x",
  label_key: "reports.dimensions.x",
  kind: "string",
  labels: "none",
  i18n_values: null,
  filter_type: "string",
  options_source: null,
  time_field: false,
  ops: ["eq"],
  enum_values: [],
  ...over,
});

const SOURCE_FIXTURE = {
  key: "tickets",
  label_key: "reports.sources.tickets",
  default_time_field: "created",
  measures: [],
  dimensions: [
    dim({ key: "queue", kind: "id", filter_type: "id", options_source: "queue", labels: "queue", ops: ["eq", "in", "neq", "not_in"] }),
    dim({ key: "owner", kind: "id", filter_type: "id", labels: "user", ops: ["eq", "in"] }),
    dim({ key: "channel", kind: "enum", filter_type: "enum", i18n_values: "reports.values.channel", enum_values: ["email", "phone"], ops: ["eq", "in"] }),
    dim({ key: "created", kind: "date", filter_type: "datetime", time_field: true, ops: ["after", "before"] }),
    dim({ key: "created_hour", kind: "hour", filter_type: null, ops: [] }),
    dim({ key: "customer", kind: "string", filter_type: "string", labels: "customer_company", ops: ["eq"] }),
  ],
  filter_fields: [
    dim({ key: "reopened", kind: "bool", filter_type: "bool", ops: ["is_false", "is_true"] }),
    dim({ key: "created", kind: "datetime", filter_type: "datetime", ops: ["after"] }),
  ],
};

describe("fieldsFromTicketCatalog", () => {
  it("maps ticket catalog fields with options sources", () => {
    const defs = fieldsFromTicketCatalog(
      [
        { key: "owner_id", type: "id", ops: ["eq", "in"], label_key: "ticketFilter.fields.owner_id", label: null, enum_values: [], options_source: null },
        { key: "df.Area", type: "string", ops: ["eq", "in"], label_key: null, label: "Bereich", enum_values: [], options_source: "DynamicField_Area" },
        { key: "queue_id", type: "id", ops: ["eq"], label_key: "ticketFilter.fields.queue_id", label: null, enum_values: [], options_source: "queue" },
        { key: "tn", type: "string", ops: ["contains", "eq"], label_key: "ticketFilter.fields.tn", label: null, enum_values: [], options_source: null },
      ],
      t,
    );
    expect(defs.map((d) => [d.key, d.optionsSource, d.label])).toEqual([
      ["owner_id", "user", "ticketFilter.fields.owner_id"],
      ["df.Area", "df:Area", "Bereich"],
      ["queue_id", "queue", "ticketFilter.fields.queue_id"],
      ["tn", null, "ticketFilter.fields.tn"],
    ]);
    expect(defs[3].ops).toEqual(["contains", "eq"]);
  });

  it("labels enum values", () => {
    const [def] = fieldsFromTicketCatalog(
      [{ key: "channel", type: "enum", ops: ["eq"], label_key: "ticketFilter.fields.channel", label: null, enum_values: ["email"], options_source: null }],
      t,
    );
    expect(def.enumValues).toEqual([{ value: "email", label: "reports.values.channel.email" }]);
  });
});

describe("fieldsFromSource", () => {
  it("maps report source dimensions and filter-only fields, skipping unfilterable ones", () => {
    const defs = fieldsFromSource(SOURCE_FIXTURE as never, t);
    expect(defs.map((d) => d.key)).toEqual(["queue", "owner", "channel", "created", "customer", "reopened"]);
    expect(defs.find((d) => d.key === "channel")?.enumValues[0]).toEqual({ value: "email", label: "reports.values.channel.email" });
  });

  it("takes operators from the API and options from options_source or user labels", () => {
    const defs = fieldsFromSource(SOURCE_FIXTURE as never, t);
    const by = Object.fromEntries(defs.map((d) => [d.key, d]));
    expect(by.queue.ops).toEqual(["eq", "in", "neq", "not_in"]);
    expect(by.queue.optionsSource).toBe("queue");
    expect(by.owner.optionsSource).toBe("user");
    expect(by.customer.optionsSource).toBeNull();
    expect(by.created.ops).toEqual(["after", "before"]);
    expect(by.created.label).toBe("reports.dimensions.x");
  });
});
