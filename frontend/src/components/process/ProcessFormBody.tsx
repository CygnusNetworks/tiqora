import { useState } from "react";
import { useChatAttachments } from "@/components/agent/telegram/useChatAttachments";
import { useTranslation } from "react-i18next";
import { displayTimeZone } from "@/lib/timeZone";
import { Button } from "@/components/ui/Button";
import { validateFieldValues } from "@/components/fields";
import type { FieldErrors, FieldValue, FieldValues } from "@/components/fields";
import { ProcessDialogForm } from "./ProcessDialogForm";
import type { ProcessDialogFormData } from "@/lib/processApi";
import { ArticleUploads } from "./ArticleUploads";
import { initialProcessValues, processFieldDefs, toSubmitValues, type ProcessScope } from "./processForm";
import { useProcessFormOptionsState, type ProcessOptionsTarget } from "./useProcessFormOptions";

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
  fieldTestId,
  scope = "agent",
  optionsTarget,
}: {
  form: ProcessDialogFormData;
  onSubmit: (fieldValues: Record<string, unknown>) => void;
  pending: boolean;
  /** Message of the failed submit, shown above the button. */
  error?: string | null;
  onCancel?: () => void;
  testIdPrefix: string;
  /** Test id prefix of the fields; defaults to `<testIdPrefix>-field`. */
  fieldTestId?: string;
  /** Agent form (default) or customer portal form: the portal drops the customer search and ticket attachments. */
  scope?: ProcessScope;
  /** Where to ask for refreshed select options; without it the options stay as the form came. */
  optionsTarget?: ProcessOptionsTarget;
}) {
  const { t } = useTranslation();
  const [values, setValues] = useState<FieldValues>(() => initialProcessValues(form));
  const [errors, setErrors] = useState<FieldErrors>({});
  const uploads = useChatAttachments();
  const hasUploads = form.fields.some((f) => f.kind === "article" && f.uploads);
  const { options: optionsByKey, refreshing } = useProcessFormOptionsState(optionsTarget, form, values);

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
    const found = validateFieldValues(processFieldDefs(form, t, scope), values, t);
    setErrors(found);
    if (Object.keys(found).length > 0) return;
    onSubmit(toSubmitValues(form, values, displayTimeZone(), { uploads: uploads.payload, scope }));
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
        testId={fieldTestId ?? `${testIdPrefix}-field`}
        scope={scope}
        optionsByKey={optionsByKey}
        uploads={hasUploads ? <ArticleUploads uploads={uploads} disabled={pending} testId={`${testIdPrefix}-uploads`} /> : undefined}
      />
      {refreshing && (
        <p className="text-xs text-muted" role="status" data-testid={`${testIdPrefix}-options-refreshing`}>
          {t("process.form.optionsRefreshing")}
        </p>
      )}
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
        <Button type="submit" variant="primary" disabled={pending || uploads.encoding} data-testid={`${testIdPrefix}-submit`}>
          {form.submit_button_text || t("process.form.submit")}
        </Button>
      </div>
    </form>
  );
}
