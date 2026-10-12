import { describe, it, expect } from "vitest";
import type { DesignerCatalog } from "@/lib/processDesignerApi";
import {
  actionFieldDefs,
  buildActionConfig,
  groupOfAction,
  paramGroups,
  readActionConfig,
  type CatalogAction,
} from "./actionForm";

const stateSet: CatalogAction = {
  name: "TicketStateSet",
  module: "Kernel::System::ProcessManagement::TransitionAction::TicketStateSet",
  supported: true,
  tiqora_only: false,
  params: [
    { name: "State", kind: "state", required: true, one_of: ["State", "StateID"], list_value: false },
    { name: "StateID", kind: "state", required: true, one_of: ["State", "StateID"], list_value: false },
    { name: "PendingTime", kind: "datetime", required: false, one_of: [], list_value: false },
  ],
};
const catalog = {
  actions: [stateSet],
  fields: [
    { name: "State", label: "State", kind: "select", dialog: true, condition: true, options: [{ value: "open", label: "open" }, { value: "closed", label: "closed" }] },
    { name: "DynamicField_Prio", label: "Prio", kind: "Text", dialog: true, condition: true, options: [] },
  ],
} as unknown as DesignerCatalog;
const entity = (cfg: Record<string, unknown>) => ({ Module: stateSet.module, Config: cfg });

describe("actionForm", () => {
  it("merges one_of keys into one group and skips UserID", () => {
    const withUser: CatalogAction = {
      ...stateSet,
      params: [...stateSet.params, { name: "UserID", kind: "user", required: false, one_of: [], list_value: false }],
    };
    const groups = paramGroups(withUser);
    expect(groups.map((g) => g.id)).toEqual(["State|StateID", "PendingTime"]);
    expect(groups[0].keys).toEqual(["State", "StateID"]);
  });

  it("TicketStateSet reads State and writes it back", () => {
    const state = readActionConfig(stateSet, entity({ State: "closed" }));
    expect(state.fields["State|StateID"]).toEqual({ key: "State", value: "closed" });
    expect(state.raw).toEqual([]);
    state.fields["State|StateID"] = { key: "State", value: "open" };
    expect(buildActionConfig(stateSet, entity({ State: "closed" }), state)).toEqual(entity({ State: "open" }));
  });

  it("uses the ID alternative when only StateID is stored and keeps numbers numeric", () => {
    const base = entity({ StateID: 4 });
    const state = readActionConfig(stateSet, base);
    expect(state.fields["State|StateID"]).toEqual({ key: "StateID", value: "4" });
    expect(buildActionConfig(stateSet, base, state)).toEqual(base);
  });

  it("never writes a locked (non-scalar) row under another key", () => {
    const base = entity({ Nested: { a: [1, 2] } });
    const state = readActionConfig(stateSet, base);
    state.raw[0].key = "Renamed";
    expect(Object.keys(buildActionConfig(stateSet, base, state).Config as object)).toEqual([]);
  });

  it("round-trips raw keys including DynamicField_* and non-scalar values", () => {
    const base = entity({ State: "open", DynamicField_Foo: "bar", Extra: 7, Nested: { a: [1, 2] } });
    const state = readActionConfig(stateSet, base);
    expect(state.raw.map((r) => r.key)).toEqual(["DynamicField_Foo", "Extra", "Nested"]);
    expect(state.raw[2].locked).toBe(true);
    expect(buildActionConfig(stateSet, base, state)).toEqual(base);
    state.raw[0].value = "baz";
    state.raw.push({ key: "DynamicField_New", value: "1", locked: false });
    expect(buildActionConfig(stateSet, base, state).Config).toEqual({
      State: "open",
      DynamicField_Foo: "baz",
      Extra: 7,
      Nested: { a: [1, 2] },
      DynamicField_New: "1",
    });
  });

  it("unknown module shows only raw rows and keeps everything", () => {
    const base = { Module: "Kernel::System::ProcessManagement::TransitionAction::Custom", Config: { A: "1", UserID: 3 } };
    const state = readActionConfig(undefined, base);
    expect(Object.keys(state.fields)).toEqual([]);
    expect(state.raw).toEqual([{ key: "A", value: "1", locked: false }]);
    expect(state.userId).toBe("3");
    expect(buildActionConfig(undefined, base, state)).toEqual(base);
  });

  it("keeps a free-text smart tag as typed value", () => {
    const state = readActionConfig(stateSet, entity({}));
    state.fields["State|StateID"].value = "<OTRS_TICKET_State>";
    state.userId = "<OTRS_OWNER_UserID>";
    expect(buildActionConfig(stateSet, entity({}), state).Config).toEqual({
      State: "<OTRS_TICKET_State>",
      UserID: "<OTRS_OWNER_UserID>",
    });
  });

  it("writes a numeric UserID as number and omits empty values", () => {
    const state = readActionConfig(stateSet, entity({}));
    state.userId = "12";
    expect(buildActionConfig(stateSet, entity({}), state).Config).toEqual({ UserID: 12 });
  });

  it("derives FieldDefs with catalogue suggestions and groups module names", () => {
    const defs = actionFieldDefs(stateSet, catalog);
    expect(defs[0]).toMatchObject({ key: "State|StateID", kind: "text", required: true });
    expect(defs[0].options?.map((o) => o.value)).toEqual(["open", "closed"]);
    expect(groupOfAction("TicketArticleCreate")).toBe("article");
    expect(groupOfAction("DynamicFieldSet")).toBe("dynamicField");
    expect(groupOfAction("AppointmentCreate")).toBe("calendar");
    expect(groupOfAction("TicketStateSet")).toBe("ticket");
    expect(groupOfAction("LinkAdd")).toBe("other");
  });
});
