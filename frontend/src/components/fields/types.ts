import type { ComponentType } from "react";

export type FieldKind =
  | "text"
  | "textarea"
  | "number"
  | "boolean"
  | "select"
  | "multiselect"
  | "date"
  | "datetime"
  | "secret"
  | "tags";
export type FieldValue = string | number | boolean | string[] | null;
// date: "YYYY-MM-DD"; datetime: "YYYY-MM-DDTHH:MM" (wall time in the agent's display zone)
export type FieldValues = Record<string, FieldValue>;
export type FieldErrors = Record<string, string>;
export type FieldOption = { value: string; label: string; disabled?: boolean; hint?: string };
export type FieldDef = {
  key: string;
  kind: FieldKind;
  label: string; // already translated
  help?: string;
  required?: boolean;
  readOnly?: boolean;
  placeholder?: string;
  options?: FieldOption[]; // static options / tag suggestions
  optionsKey?: string; // key for POST /reference/field-options; loaded options win over `options`
  allowEmpty?: boolean; // select: offers the "–" entry
  min?: number;
  max?: number;
  step?: number;
  maxLength?: number;
  secretSet?: boolean; // secret: a value is stored on the server
};
export type FieldKindProps = {
  def: FieldDef;
  value: FieldValue;
  onChange: (value: FieldValue) => void;
  id: string;
  testId: string;
  disabled?: boolean;
  invalid?: boolean;
  options: FieldOption[];
};
export type FieldKindRegistration = { kind: string; component: ComponentType<FieldKindProps> };
