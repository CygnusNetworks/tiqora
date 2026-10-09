import { describe, expect, it } from "vitest";
import i18n from "@/i18n";
import type { ProcessDialogFieldData, ProcessDialogFormData } from "@/lib/processApi";
import {
  ARTICLE_BODY_KEY,
  ARTICLE_SUBJECT_KEY,
  initialProcessValues,
  processFieldDefs,
  processLabel,
  toSubmitValues,
} from "./processForm";

const t = i18n.getFixedT("de");
const base = { description_short: "", description_long: "", default: null, options: [] };
const form: ProcessDialogFormData = {
  process_entity_id: "P",
  activity_dialog_entity_id: "AD",
  name: "Bestellung",
  description_short: "",
  description_long: "",
  submit_button_text: "",
  submit_advice_text: "",
  fields: [
    { ...base, name: "Queue", kind: "select", label: "Queue", required: true, options: [{ value: "3", label: "Support" }] },
    { ...base, name: "Article", kind: "article", label: "Article", required: true },
    { ...base, name: "PendingTime", kind: "pending_time", label: "PendingTime", required: false },
    {
      ...base,
      name: "DynamicField_Tags",
      kind: "multiselect",
      label: "Schlagworte",
      required: false,
      options: [
        { value: "a", label: "A" },
        { value: "b", label: "B" },
      ],
    },
  ],
};

describe("processForm", () => {
  it("splits the article into subject and body", () => {
    const defs = processFieldDefs(form, t);
    expect(defs.map((d) => d.key)).toEqual(["Queue", ARTICLE_SUBJECT_KEY, ARTICLE_BODY_KEY, "PendingTime", "DynamicField_Tags"]);
    expect(defs.find((d) => d.key === ARTICLE_BODY_KEY)?.kind).toBe("textarea");
    expect(defs.find((d) => d.key === ARTICLE_SUBJECT_KEY)?.label).toBe("Betreff");
    expect(defs.find((d) => d.key === ARTICLE_SUBJECT_KEY)?.required).toBe(true);
    expect(defs.find((d) => d.key === "PendingTime")?.kind).toBe("datetime");
    expect(defs.find((d) => d.key === "Queue")?.allowEmpty).toBe(false);
  });

  it("labels ticket attributes in German and keeps DF labels", () => {
    const defs = processFieldDefs(form, t);
    expect(defs[0].label).toBe("Queue");
    expect(defs.find((d) => d.key === "PendingTime")?.label).toBe("Erinnerung am");
    expect(defs.find((d) => d.key === "DynamicField_Tags")?.label).toBe("Schlagworte");
    const state: ProcessDialogFieldData = { ...base, name: "State", kind: "select", label: "State", required: false };
    expect(processLabel(state, t)).toBe("Status");
  });

  it("marks unsupported fields read-only with a note", () => {
    const f: ProcessDialogFormData = {
      ...form,
      fields: [{ ...base, name: "Attachments", kind: "unsupported", label: "Attachments", required: true }],
    };
    const [def] = processFieldDefs(f, t);
    expect(def.readOnly).toBe(true);
    expect(def.help).toBe("Dieses Feld kann hier noch nicht ausgefüllt werden.");
    expect(toSubmitValues(f, { Attachments: "x" })).toEqual({});
  });

  it("translates the Lock options", () => {
    const f: ProcessDialogFormData = {
      ...form,
      fields: [
        { ...base, name: "Lock", kind: "select", label: "Lock", required: false, options: [{ value: "lock", label: "lock" }, { value: "unlock", label: "unlock" }] },
      ],
    };
    expect(processFieldDefs(f, t)[0].options?.map((o) => o.label)).toEqual(["gesperrt", "frei"]);
  });

  it("maps values back to the API contract", () => {
    const v = toSubmitValues(
      form,
      { Queue: "3", [ARTICLE_SUBJECT_KEY]: "S", [ARTICLE_BODY_KEY]: "B", PendingTime: "2026-10-12T10:00", DynamicField_Tags: ["a", "b"] },
      "Europe/Berlin",
    );
    expect(v).toEqual({ Queue: "3", Subject: "S", Body: "B", PendingTime: "2026-10-12T08:00:00.000Z", DynamicField_Tags: ["a", "b"] });
  });

  it("leaves empty values out", () => {
    expect(toSubmitValues(form, { Queue: "", [ARTICLE_SUBJECT_KEY]: "", [ARTICLE_BODY_KEY]: "", PendingTime: "", DynamicField_Tags: [] })).toEqual({});
  });

  it("seeds initial values from the defaults", () => {
    const f: ProcessDialogFormData = {
      ...form,
      fields: [
        { ...base, name: "Queue", kind: "select", label: "Queue", required: true, default: 3 },
        { ...base, name: "DynamicField_Ok", kind: "boolean", label: "Ok", required: false, default: "1" },
        { ...base, name: "DynamicField_Tags", kind: "multiselect", label: "T", required: false, default: ["a"] },
        { ...base, name: "Article", kind: "article", label: "Article", required: false },
      ],
    };
    expect(initialProcessValues(f)).toEqual({
      Queue: "3",
      DynamicField_Ok: true,
      DynamicField_Tags: ["a"],
      [ARTICLE_SUBJECT_KEY]: "",
      [ARTICLE_BODY_KEY]: "",
    });
  });
});
