import { describe, it, expect } from "vitest";
import type { CatalogField } from "@/lib/processDesignerApi";
import { buildDialogConfig, editorKindOf, fieldHints, newFieldModel, readDialog } from "./dialogFields";

const f = (name: string, kind: string, options: { value: string; label: string }[] = []): CatalogField => ({
  name,
  label: name,
  kind,
  dialog: true,
  condition: false,
  options,
});

type Cfg = {
  FieldOrder: string[];
  Fields: Record<string, { Config?: unknown } & Record<string, unknown>>;
  Other?: string;
  RequiredLock?: string;
  Interface?: string[];
};

describe("editorKindOf", () => {
  it("maps each field type", () => {
    expect(editorKindOf(f("Queue", "select", [{ value: "Raw", label: "Raw" }]), "Queue")).toBe("select");
    expect(editorKindOf(f("DynamicField_A", "Dropdown"), "DynamicField_A")).toBe("select");
    expect(editorKindOf(f("DynamicField_M", "Multiselect"), "DynamicField_M")).toBe("select");
    expect(editorKindOf(f("DynamicField_D", "Date"), "DynamicField_D")).toBe("date");
    expect(editorKindOf(f("DynamicField_T", "DateTime"), "DynamicField_T")).toBe("datetime");
    expect(editorKindOf(f("DynamicField_C", "Checkbox"), "DynamicField_C")).toBe("checkbox");
    expect(editorKindOf(f("DynamicField_X", "TextArea"), "DynamicField_X")).toBe("textarea");
    expect(editorKindOf(f("Title", "text"), "Title")).toBe("text");
    expect(editorKindOf(f("Article", "article"), "Article")).toBe("none");
    expect(editorKindOf(undefined, "Foo")).toBe("text");
  });
});

describe("dialog config round trip", () => {
  const stored = {
    DescriptionShort: "Erfassen",
    Fields: {
      Title: { Display: 2, DefaultValue: "", Extra: "keep" },
      Queue: { Display: "0", DefaultValue: "Raw" },
      Article: { Display: "1", Config: { CommunicationChannel: "Email", IsVisibleForCustomer: "1", TimeUnits: "2" } },
    },
    FieldOrder: ["Queue", "Title"],
    Interface: "AgentInterface",
    RequiredLock: "1",
    Other: "x",
  };

  it("reads order (stored first, missing appended) and normalises", () => {
    const m = readDialog(stored);
    expect(m.order).toEqual(["Queue", "Title", "Article"]);
    expect(m.interfaces).toEqual(["AgentInterface"]);
    expect(m.fields.Title.display).toBe("2");
    expect(m.fields.Article.article).toEqual({ channel: "Email", visibleForCustomer: true, timeUnits: "2" });
    expect(m.requiredLock).toBe(true);
  });

  it("writes strings only, keeps unknown keys and syncs FieldOrder", () => {
    const m = readDialog(stored);
    m.order = ["Article", "Title"]; // Queue removed
    m.fields.Title.display = "1";
    m.fields.Article.article!.timeUnits = "1";
    const out = buildDialogConfig(stored, m, true) as Cfg;
    expect(out.FieldOrder).toEqual(["Article", "Title"]);
    expect(Object.keys(out.Fields)).toEqual(["Article", "Title"]);
    expect(out.Fields.Title).toMatchObject({ Display: "1", Extra: "keep", DefaultValue: "" });
    expect(out.Fields.Article.Config).toEqual({ CommunicationChannel: "Email", IsVisibleForCustomer: "1", TimeUnits: "1" });
    expect(out.Other).toBe("x");
    expect(out.RequiredLock).toBe("1");
    expect(out.Interface).toEqual(["AgentInterface"]);
  });

  it("does not add TimeUnits when time accounting is off", () => {
    const m = readDialog({ Fields: { Article: { Display: "1" } }, FieldOrder: ["Article"] });
    const out = buildDialogConfig({}, m, false) as Cfg;
    expect(out.Fields.Article.Config).toEqual({ CommunicationChannel: "Internal", IsVisibleForCustomer: "0" });
  });
});

describe("fieldHints", () => {
  it("warns about Display 1 on Queue/State/Lock/Priority", () => {
    expect(fieldHints("Queue", { ...newFieldModel("Queue"), display: "1" }, ["AgentInterface"])).toContain("queueRequired");
    expect(fieldHints("Queue", { ...newFieldModel("Queue"), display: "2" }, ["AgentInterface"])).not.toContain("queueRequired");
    expect(fieldHints("Title", newFieldModel("Title"), ["AgentInterface"])).toEqual([]);
  });
  it("tells that customers do not see Owner", () => {
    const m = { ...newFieldModel("Owner"), display: "2" as const };
    expect(fieldHints("Owner", m, ["AgentInterface", "CustomerInterface"])).toContain("customerHidden");
    expect(fieldHints("Owner", m, ["AgentInterface"])).not.toContain("customerHidden");
  });
  it("hidden field without default needs one", () => {
    const m = { ...newFieldModel("Title"), display: "0" as const };
    expect(fieldHints("Title", m, [])).toEqual(["hiddenNeedsDefault"]);
    expect(fieldHints("Title", { ...m, defaultValue: "x" }, [])).toEqual([]);
  });
});
