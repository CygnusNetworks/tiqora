import { useState } from "react";
import { useTranslation } from "react-i18next";
import { displayTimeZone } from "@/lib/timeZone";
import { Button } from "@/components/ui/Button";
import { validateFieldValues } from "@/components/fields";
import type { FieldErrors, FieldValue, FieldValues } from "@/components/fields";
import { ProcessDialogForm } from "./ProcessDialogForm";
import type { ProcessDialogFormData } from "@/lib/processApi";
import { initialProcessValues, processFieldDefs, toSubmitValues } from "./processForm";

/**
 * A loaded process form with its values, validation and buttons: shared by the start forms
 * (agent and portal) and the portal activity dialog. Mount it once the form is loaded (`key` it
 * by the form so a reload starts from the defaults); `onSubmit` gets the submit-shaped values.
 */
export function ProcessFormBody({
  form,
  onSubmit,
  pending,
  error,
  onCancel,
  testIdPrefix,
}: {
  form: ProcessDialogFormData;
  onSubmit: (fieldValues: Record<string, unknown>) => void;
  pending: boolean;
  /** Message of the failed submit, shown above the button. */
  error?: string | null;
  onCancel?: () => void;
  testIdPrefix: string;
}) {
  const { t } = useTranslation();
  const [values, setValues] = useState<FieldValues>(() => initialProcessValues(form));
  const [errors, setErrors] = useState<FieldErrors>({});

  function setValue(key: string, value: FieldValue) {
    setValues((v) => ({ ...v, [key]: value }));
    setErrors((e) => {
      if (!(key in e)) return e;
      const rest = { ...e };
      delete rest[key];
      return rest;
    });
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const found = validateFieldValues(processFieldDefs(form, t), values, t);
    setErrors(found);
    if (Object.keys(found).length > 0) return;
    onSubmit(toSubmitValues(form, values, displayTimeZone()));
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3" data-testid={`${testIdPrefix}-form`}>
      {form.description_long && <p className="text-sm text-muted">{form.description_long}</p>}
      <ProcessDialogForm
        form={form}
        values={values}
        onChange={setValue}
        errors={errors}
        disabled={pending}
        testId={`${testIdPrefix}-field`}
      />
      {error && (
        <p className="text-sm text-danger" role="alert" data-testid={`${testIdPrefix}-error`}>
          {error}
        </p>
      )}
      {form.submit_advice_text && <p className="text-xs text-muted">{form.submit_advice_text}</p>}
      <div className="flex justify-end gap-2 border-t border-hairline pt-3">
        {onCancel && (
          <Button type="button" variant="ghost" onClick={onCancel}>
            {t("process.form.cancel")}
          </Button>
        )}
        <Button type="submit" variant="primary" disabled={pending} data-testid={`${testIdPrefix}-submit`}>
          {form.submit_button_text || t("process.form.submit")}
        </Button>
      </div>
    </form>
  );
}
