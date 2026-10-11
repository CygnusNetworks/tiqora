import { describe, expect, it } from "vitest";
import i18n from "@/i18n";
import { validateFieldValues } from "@/components/fields";
import { fromZonedInputValue } from "@/lib/timeZone";
import type { ProcessDialogFieldData, ProcessDialogFormData } from "@/lib/processApi";
import {
  ARTICLE_BODY_KEY,
  ARTICLE_SUBJECT_KEY,
  initialPendingTime,
  initialProcessValues,
  processFieldDefs,
  processLabel,
  toSubmitValues,
} from "./processForm";

const t = i18n.getFixedT("de");
const base = {
  description_short: "",
  description_long: "",
  default: null,
  options: [],
  time_units: "0" as const,
  uploads: false,
};
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

  it("leaves empty ticket attributes out but sends [] for an emptied dynamic field", () => {
    expect(toSubmitValues(form, { Queue: "", [ARTICLE_SUBJECT_KEY]: "", [ARTICLE_BODY_KEY]: "", PendingTime: "", DynamicField_Tags: [] })).toEqual({
      DynamicField_Tags: [],
    });
  });

  it("clears every kind of editable dynamic field with an explicit empty list", () => {
    const dfs: ProcessDialogFormData = {
      ...form,
      fields: [
        { ...base, name: "Title", kind: "text", label: "Title", required: false },
        { ...base, name: "DynamicField_Txt", kind: "text", label: "Txt", required: false },
        { ...base, name: "DynamicField_Sel", kind: "select", label: "Sel", required: false },
        { ...base, name: "DynamicField_Day", kind: "date", label: "Day", required: false },
        { ...base, name: "DynamicField_At", kind: "datetime", label: "At", required: false },
        { ...base, name: "DynamicField_Reg", kind: "unsupported", label: "Reg", required: false },
      ],
    };
    // The user emptied the prefilled fields (initial values come from the ticket's values).
    const values = { Title: "", DynamicField_Txt: "", DynamicField_Sel: "", DynamicField_Day: "", DynamicField_At: "" };
    expect(toSubmitValues(dfs, values, "Europe/Berlin")).toEqual({
      DynamicField_Txt: [],
      DynamicField_Sel: [],
      DynamicField_Day: [],
      DynamicField_At: [],
    });
    // A value still goes out as a one-element list.
    expect(toSubmitValues(dfs, { ...values, DynamicField_Txt: "x" })).toMatchObject({ DynamicField_Txt: ["x"] });
  });

  it("round-trips a prefilled current value untouched", () => {
    const f: ProcessDialogFormData = {
      ...form,
      fields: [
        { ...base, name: "Queue", kind: "select", label: "Queue", required: false, default: "3", options: [{ value: "3", label: "Support" }] },
        { ...base, name: "DynamicField_Txt", kind: "text", label: "Txt", required: false, default: "stored" },
        { ...base, name: "DynamicField_Tags", kind: "multiselect", label: "T", required: false, default: ["a", "b"] },
      ],
    };
    expect(toSubmitValues(f, initialProcessValues(f))).toEqual({
      Queue: "3",
      DynamicField_Txt: ["stored"],
      DynamicField_Tags: ["a", "b"],
    });
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

  it("prefills PendingTime with now + the configured offset, like Znuny", () => {
    const now = new Date("2026-10-09T10:00:00Z");
    expect(initialPendingTime("3600", now, "Europe/Berlin")).toBe("2026-10-09T13:00");
    expect(initialPendingTime(7200, now, "Europe/Berlin")).toBe("2026-10-09T14:00");
    // no / zero default: one day
    expect(initialPendingTime(null, now, "Europe/Berlin")).toBe("2026-10-10T12:00");
    expect(initialPendingTime("0", now, "Europe/Berlin")).toBe("2026-10-10T12:00");
    // a timestamp default is the date itself
    expect(initialPendingTime("2026-12-01 08:30:00", now, "Europe/Berlin")).toBe("2026-12-01T09:30");

    const f: ProcessDialogFormData = {
      ...form,
      fields: [{ ...base, name: "PendingTime", kind: "pending_time", label: "PendingTime", required: true, default: "3600" }],
    };
    const start = Date.now();
    const v = initialProcessValues(f).PendingTime as string;
    expect(v).not.toBe("");
    const ahead = fromZonedInputValue(v)!.getTime() - start;
    expect(ahead).toBeGreaterThan(3500 * 1000 - 60_000);
    expect(ahead).toBeLessThan(3600 * 1000 + 60_000);
  });

  it("does not require PendingTime on the client for Display 2 (only a pending target needs it)", () => {
    const f: ProcessDialogFormData = {
      ...form,
      fields: [{ ...base, name: "PendingTime", kind: "pending_time", label: "PendingTime", required: true }],
    };
    const [def] = processFieldDefs(f, t);
    expect(def.required).toBe(false);
    expect(validateFieldValues([def], { PendingTime: "" }, t)).toEqual({});
  });
});
