import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/lib/api";
import { processApi } from "@/lib/processApi";
import { displayTimeZone } from "@/lib/timeZone";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { validateFieldValues } from "@/components/fields";
import type { FieldErrors, FieldValue, FieldValues } from "@/components/fields";
import { ProcessDialogForm } from "@/components/process/ProcessDialogForm";
import { initialProcessValues, processFieldDefs, toSubmitValues } from "@/components/process/processForm";

export function ActivityDialogModal({
  ticketId,
  activityDialogEntityId,
  open,
  onClose,
}: {
  ticketId: number;
  activityDialogEntityId: string;
  open: boolean;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [values, setValues] = useState<FieldValues>({});
  const [errors, setErrors] = useState<FieldErrors>({});

  const formQ = useQuery({
    queryKey: ["process", "ticket", ticketId, "dialog-form", activityDialogEntityId],
    queryFn: ({ signal }) => processApi.dialogForm(ticketId, activityDialogEntityId, signal),
    enabled: open,
    // the options are ACL-filtered per open; never show a stale form
    gcTime: 0,
  });

  useEffect(() => {
    if (formQ.data) {
      setValues(initialProcessValues(formQ.data));
      setErrors({});
    }
  }, [formQ.data]);

  const form = formQ.data;

  const submitM = useMutation({
    mutationFn: () => {
      if (!form) throw new Error("form not loaded");
      return processApi.submit(ticketId, {
        activity_dialog_entity_id: activityDialogEntityId,
        field_values: toSubmitValues(form, values, displayTimeZone()),
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["process", "ticket", ticketId, "state"] });
      void queryClient.invalidateQueries({ queryKey: ["tickets", ticketId] });
      onClose();
    },
  });

  if (!open) return null;

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
    submitM.mutate();
  }

  return (
    <Dialog open={open} onClose={onClose} title={form?.name ?? t("process.form.loadError")} className="max-w-lg">
      {formQ.isLoading ? (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      ) : formQ.isError || !form ? (
        <p className="text-sm text-danger" data-testid="process-dialog-load-error">
          {t("process.form.loadError")}
        </p>
      ) : (
        <form
          data-testid="process-dialog-form"
          onSubmit={handleSubmit}
          className="flex max-h-[70vh] flex-col gap-3 overflow-y-auto"
        >
          {form.description_long && <p className="text-sm text-muted">{form.description_long}</p>}
          <ProcessDialogForm
            form={form}
            values={values}
            onChange={setValue}
            errors={errors}
            disabled={submitM.isPending}
            testId="process-field"
          />

          {Object.keys(errors).length > 0 && (
            <p className="text-sm text-danger" data-testid="process-dialog-required-error">
              {t("process.dialog.requiredError")}
            </p>
          )}

          {submitM.isError && (
            <p className="text-sm text-danger" role="alert" data-testid="process-dialog-submit-error">
              {submitM.error instanceof ApiError ? submitM.error.message : t("process.form.submitError")}
            </p>
          )}

          {form.submit_advice_text && <p className="text-xs text-muted">{form.submit_advice_text}</p>}

          <div className="flex justify-end gap-2 border-t border-hairline pt-3">
            <Button type="button" variant="ghost" onClick={onClose}>
              {t("process.form.cancel")}
            </Button>
            <Button type="submit" variant="primary" disabled={submitM.isPending} data-testid="process-dialog-submit">
              {form.submit_button_text || t("process.form.submit")}
            </Button>
          </div>
        </form>
      )}
    </Dialog>
  );
}
