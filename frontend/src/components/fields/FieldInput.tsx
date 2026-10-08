import { useId } from "react";
import { useTranslation } from "react-i18next";
import { FIELD_KINDS, registerCoreFieldKinds } from "./kinds";
import type { FieldDef, FieldOption, FieldValue } from "./types";

registerCoreFieldKinds();

/** One control for a `FieldDef`, picked from the `core.fields.kinds` registry. */
export function FieldInput({
  def,
  value,
  onChange,
  testId,
  options,
  disabled,
  invalid,
  id,
  labelId,
  describedBy,
}: {
  def: FieldDef;
  value: FieldValue;
  onChange: (v: FieldValue) => void;
  testId: string;
  options?: FieldOption[];
  disabled?: boolean;
  invalid?: boolean;
  /** DOM id for label association; generated when omitted. */
  id?: string;
  labelId?: string;
  describedBy?: string;
}) {
  const { t } = useTranslation();
  const generated = useId();
  const reg = FIELD_KINDS.get(def.kind);
  if (!reg) {
    return (
      <p data-testid={testId} className="text-xs text-muted">
        {t("fields.unsupported")}
      </p>
    );
  }
  const Component = reg.component;
  return (
    <Component
      def={def}
      value={value}
      onChange={onChange}
      id={id ?? generated}
      testId={testId}
      disabled={disabled}
      invalid={invalid}
      required={def.required}
      describedBy={describedBy}
      labelId={labelId}
      options={options ?? def.options ?? []}
    />
  );
}
