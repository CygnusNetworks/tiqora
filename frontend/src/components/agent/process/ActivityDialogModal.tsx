import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, ApiError, type ActivityDialogFieldOut } from "@/lib/api";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { SelectField } from "@/components/ui/SelectField";
import { Spinner } from "@/components/ui/Spinner";
import { flattenQueues } from "@/components/agent/QueueTree";
import {
  ARTICLE_FIELD_NAME,
  QUEUE_FIELD_NAME,
  buildInitialFieldValues,
  isFieldRequired,
  missingRequiredFields,
  type ArticleFieldValue,
  type FieldValues,
} from "./processDialogFields";

const inputClass =
  "w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink placeholder:text-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent focus:border-accent";
const labelClass = "mb-1 block text-xs font-medium uppercase tracking-wide text-muted";

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
  const [missing, setMissing] = useState<string[]>([]);

  const dialogQ = useQuery({
    queryKey: ["process", "activity-dialog", activityDialogEntityId],
    queryFn: ({ signal }) => api.getActivityDialog(activityDialogEntityId, signal),
    enabled: open,
  });

  const queuesQ = useQuery({
    queryKey: ["queues"],
    queryFn: () => api.listQueues(),
    enabled: open && Boolean(dialogQ.data?.fields[QUEUE_FIELD_NAME]),
  });

  useEffect(() => {
    if (dialogQ.data) {
      setValues(buildInitialFieldValues(dialogQ.data.field_order, dialogQ.data.fields));
      setMissing([]);
    }
  }, [dialogQ.data]);

  const submitM = useMutation({
    mutationFn: () =>
      api.submitActivityDialog(ticketId, {
        activity_dialog_entity_id: activityDialogEntityId,
        field_values: values,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["process", "ticket", ticketId, "state"] });
      void queryClient.invalidateQueries({ queryKey: ["tickets", ticketId] });
      onClose();
    },
  });

  if (!open) return null;

  const dialog = dialogQ.data;

  function setValue(name: string, value: unknown) {
    setValues((v) => ({ ...v, [name]: value }));
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!dialog) return;
    const missingNow = missingRequiredFields(dialog.field_order, dialog.fields, values);
    setMissing(missingNow);
    if (missingNow.length > 0) return;
    submitM.mutate();
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={dialog?.name ?? t("process.dialog.loadError")}
      className="max-w-lg"
    >
      {dialogQ.isLoading ? (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      ) : dialogQ.isError || !dialog ? (
        <p className="text-sm text-danger" data-testid="process-dialog-load-error">
          {t("process.dialog.loadError")}
        </p>
      ) : (
        <form
          data-testid="process-dialog-form"
          onSubmit={handleSubmit}
          className="flex max-h-[70vh] flex-col gap-3 overflow-y-auto"
        >
          {dialog.description_long && (
            <p className="text-sm text-muted">{dialog.description_long}</p>
          )}
          {dialog.field_order.map((name) => {
            const field = dialog.fields[name];
            if (!field) return null;
            return (
              <FieldInput
                key={name}
                name={name}
                field={field}
                value={values[name]}
                onChange={(v) => setValue(name, v)}
                queues={queuesQ.data ? flattenQueues(queuesQ.data) : undefined}
                invalid={missing.includes(name)}
              />
            );
          })}

          {missing.length > 0 && (
            <p className="text-sm text-danger" data-testid="process-dialog-required-error">
              {t("process.dialog.requiredError")}
            </p>
          )}

          {submitM.isError && (
            <p className="text-sm text-danger" data-testid="process-dialog-submit-error">
              {submitM.error instanceof ApiError
                ? submitM.error.message
                : t("process.dialog.submitError")}
            </p>
          )}

          <div className="flex justify-end gap-2 border-t border-hairline pt-3">
            <Button type="button" variant="ghost" onClick={onClose}>
              {t("process.dialog.cancel")}
            </Button>
            <Button
              type="submit"
              variant="primary"
              disabled={submitM.isPending}
              data-testid="process-dialog-submit"
            >
              {dialog.submit_button_text || t("process.dialog.submit")}
            </Button>
          </div>
        </form>
      )}
    </Dialog>
  );
}

function FieldInput({
  name,
  field,
  value,
  onChange,
  queues,
  invalid,
}: {
  name: string;
  field: ActivityDialogFieldOut;
  value: unknown;
  onChange: (value: unknown) => void;
  queues?: { id: number; name: string }[];
  invalid: boolean;
}) {
  const { t } = useTranslation();
  const required = isFieldRequired(field);
  const label = `${field.description_short || name}${required ? ` ${t("process.dialog.requiredMark")}` : ""}`;

  if (name === ARTICLE_FIELD_NAME) {
    const articleValue = (value as ArticleFieldValue | undefined) ?? { Subject: "", Body: "" };
    return (
      <fieldset className="space-y-2" data-testid={`process-field-${name}`}>
        <legend className={labelClass}>{label}</legend>
        <div>
          <span className={labelClass}>{t("process.dialog.articleSubject")}</span>
          <input
            className={inputClass}
            value={articleValue.Subject}
            onChange={(e) => onChange({ ...articleValue, Subject: e.target.value })}
            data-testid={`process-field-${name}-subject`}
          />
        </div>
        <div>
          <span className={labelClass}>{t("process.dialog.articleBody")}</span>
          <textarea
            className={inputClass}
            rows={4}
            value={articleValue.Body}
            onChange={(e) => onChange({ ...articleValue, Body: e.target.value })}
            data-testid={`process-field-${name}-body`}
          />
        </div>
        {invalid && (
          <p className="text-xs text-danger">{t("process.dialog.requiredError")}</p>
        )}
      </fieldset>
    );
  }

  if (name === QUEUE_FIELD_NAME) {
    return (
      <div data-testid={`process-field-${name}`}>
        <span className={labelClass}>{label}</span>
        <SelectField
          items={[
            { value: "", label: "—" },
            ...(queues ?? []).map((q) => ({ value: String(q.id), label: q.name })),
          ]}
          value={value === undefined || value === null ? "" : String(value)}
          onChange={(v) => onChange(v === "" ? "" : Number(v))}
          testId={`process-field-${name}-input`}
        />
        {invalid && <p className="mt-1 text-xs text-danger">{t("process.dialog.requiredError")}</p>}
      </div>
    );
  }

  // Fallback: plain text input for all other pseudo-fields (State, Priority,
  // Owner, Responsible, Title, ...) and every DynamicField_* field — see the
  // module docstring in processDialogFields.ts for the rationale.
  return (
    <div data-testid={`process-field-${name}`}>
      <span className={labelClass}>{label}</span>
      <input
        className={inputClass}
        value={typeof value === "string" || typeof value === "number" ? String(value) : ""}
        onChange={(e) => onChange(e.target.value)}
        data-testid={`process-field-${name}-input`}
        title={field.description_long || undefined}
      />
      {invalid && <p className="mt-1 text-xs text-danger">{t("process.dialog.requiredError")}</p>}
    </div>
  );
}
