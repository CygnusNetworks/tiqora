import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { processApi } from "@/lib/processApi";
import { displayTimeZone } from "@/lib/timeZone";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { validateFieldValues } from "@/components/fields";
import type { FieldErrors, FieldValue, FieldValues } from "@/components/fields";
import { processStartErrorMessage } from "./processStartError";
import { ProcessDialogForm } from "./ProcessDialogForm";
import { initialProcessValues, processFieldDefs, toSubmitValues } from "./processForm";

/**
 * The start dialog of one process: loads the (permission/ACL filtered) start form, validates and hands
 * the submit-shaped field values to `onSubmit`. Used by the agent "new process ticket" page and by the
 * enroll dialog (`ticketId` set), so both render and submit the same way.
 */
export function ProcessStartForm({
  processEntityId,
  ticketId,
  onSubmit,
  pending,
  error,
  onCancel,
  testIdPrefix,
  api = processApi,
}: {
  processEntityId: string;
  ticketId?: number;
  onSubmit: (fieldValues: Record<string, unknown>) => void;
  pending: boolean;
  /** Message of the failed submit, shown above the button. */
  error?: string | null;
  onCancel?: () => void;
  testIdPrefix: string;
  /** Agent (`processApi`, default) or customer portal (`portalProcessApi`) start-form source. */
  api?: Pick<typeof processApi, "startForm">;
}) {
  const { t } = useTranslation();
  const [values, setValues] = useState<FieldValues>({});
  const [errors, setErrors] = useState<FieldErrors>({});

  const formQ = useQuery({
    queryKey: [api === processApi ? "process" : "portal-process", "start-form", processEntityId, ticketId ?? null],
    queryFn: ({ signal }) => api.startForm(processEntityId, { ticketId }, signal),
    // options are ACL-filtered per open; never show a stale form
    gcTime: 0,
    retry: false,
  });
  const form = formQ.data;

  useEffect(() => {
    if (form) {
      setValues(initialProcessValues(form));
      setErrors({});
    }
  }, [form]);

  if (formQ.isLoading) {
    return (
      <div className="flex justify-center py-8">
        <Spinner />
      </div>
    );
  }
  if (formQ.isError || !form) {
    return (
      <p className="text-sm text-danger" role="alert" data-testid={`${testIdPrefix}-load-error`}>
        {processStartErrorMessage(formQ.error, t, "process.form.loadError")}
      </p>
    );
  }

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
    if (!form) return;
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
