import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { portalProcessApi } from "@/lib/portalProcessApi";
import { displayTimeZone } from "@/lib/timeZone";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Dialog } from "@/components/ui/Dialog";
import { SelectField } from "@/components/ui/SelectField";
import { Spinner } from "@/components/ui/Spinner";
import { validateFieldValues } from "@/components/fields";
import type { FieldErrors, FieldValue, FieldValues } from "@/components/fields";
import { ProcessDialogForm } from "@/components/process/ProcessDialogForm";
import { ProcessStartForm } from "@/components/process/ProcessStartForm";
import { processStartErrorMessage } from "@/components/process/processStartError";
import { initialProcessValues, processFieldDefs, toSubmitValues } from "@/components/process/processForm";

/**
 * Customer-facing process widget (Znuny CustomerTicketProcess subset): enroll a ticket through the
 * start form, run customer dialogs through the shared form. The server already narrows fields and
 * option lists to what the customer may see.
 */
export function PortalProcessWidget({ ticketId }: { ticketId: number }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [startOpen, setStartOpen] = useState(false);
  const [picked, setPicked] = useState("");
  const [dialogId, setDialogId] = useState<string | null>(null);

  const stateKey = ["portal", "process", "ticket", ticketId, "state"];

  const stateQ = useQuery({
    queryKey: stateKey,
    queryFn: ({ signal }) => portalProcessApi.ticketState(ticketId, signal),
  });

  const processesQ = useQuery({
    queryKey: ["portal", "process", "startable", ticketId],
    queryFn: ({ signal }) => portalProcessApi.listStartable({ ticketId }, signal),
    enabled: startOpen,
    retry: false,
  });

  const enrollM = useMutation({
    mutationFn: (fieldValues: Record<string, unknown>) =>
      portalProcessApi.enroll(ticketId, { process_entity_id: picked, field_values: fieldValues }),
    onSuccess: async () => {
      closeStart();
      await queryClient.invalidateQueries({ queryKey: stateKey });
      await queryClient.invalidateQueries({ queryKey: ["portal", "tickets", ticketId] });
    },
  });

  function closeStart() {
    setStartOpen(false);
    setPicked("");
    enrollM.reset();
  }

  if (stateQ.isLoading) return null;
  if (stateQ.isError || !stateQ.data) return null;

  const state = stateQ.data;
  const inProcess = Boolean(state.process_entity_id);

  return (
    <div
      className="mt-4 space-y-3 rounded-lg border border-hairline bg-surface p-4"
      data-testid="portal-process-widget"
    >
      <div className="flex items-center justify-between gap-2">
        <h2 className="font-display text-sm font-semibold text-ink">{t("process.widget.title")}</h2>
        {inProcess && state.process_name ? <Badge tone="accent">{state.process_name}</Badge> : null}
      </div>

      {!inProcess ? (
        <div>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => setStartOpen(true)}
            data-testid="portal-process-start"
          >
            {t("process.widget.startButton")}
          </Button>
        </div>
      ) : (
        <div className="space-y-2">
          <p className="text-xs text-muted">
            {t("process.widget.activity")}: <span className="font-medium text-ink">{state.activity_name}</span>
          </p>
          <ul className="flex flex-wrap gap-2">
            {(state.available_dialogs ?? []).map((d) => (
              <li key={d.entity_id}>
                <Button
                  size="sm"
                  variant="primary"
                  onClick={() => setDialogId(d.entity_id)}
                  data-testid={`portal-process-dialog-${d.entity_id}`}
                >
                  {d.name}
                </Button>
              </li>
            ))}
          </ul>
          {(state.available_dialogs ?? []).length === 0 && (
            <p className="text-xs text-muted">{t("process.widget.noCustomerDialogs")}</p>
          )}
        </div>
      )}

      <Dialog open={startOpen} onClose={closeStart} title={t("process.widget.startButton")} className="max-w-lg">
        {processesQ.isLoading ? (
          <div className="flex justify-center py-8">
            <Spinner />
          </div>
        ) : processesQ.isError ? (
          <p className="text-sm text-danger" role="alert" data-testid="portal-process-start-load-error">
            {processStartErrorMessage(processesQ.error, t, "process.start.loadError")}
          </p>
        ) : (processesQ.data ?? []).length === 0 ? (
          <p className="text-sm text-muted" data-testid="portal-process-start-empty">
            {t("process.start.empty")}
          </p>
        ) : (
          <div className="max-h-[70vh] space-y-3 overflow-y-auto">
            <div>
              <span className="mb-1 block text-xs font-medium uppercase tracking-wide text-muted">
                {t("process.start.selectLabel")}
              </span>
              <SelectField
                items={(processesQ.data ?? []).map((p) => ({ value: p.entity_id, label: p.name }))}
                value={picked || null}
                onChange={(v) => {
                  enrollM.reset();
                  setPicked(v);
                }}
                placeholder="—"
                testId="portal-process-select"
              />
            </div>
            {picked && (
              <ProcessStartForm
                key={picked}
                api={portalProcessApi}
                processEntityId={picked}
                ticketId={ticketId}
                pending={enrollM.isPending}
                error={enrollM.isError ? processStartErrorMessage(enrollM.error, t, "process.start.startError") : null}
                onSubmit={(fv) => enrollM.mutate(fv)}
                onCancel={closeStart}
                testIdPrefix="portal-process-start"
              />
            )}
          </div>
        )}
      </Dialog>

      {dialogId && (
        <PortalActivityDialog ticketId={ticketId} dialogEntityId={dialogId} onClose={() => setDialogId(null)} />
      )}
    </div>
  );
}

function PortalActivityDialog({
  ticketId,
  dialogEntityId,
  onClose,
}: {
  ticketId: number;
  dialogEntityId: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [values, setValues] = useState<FieldValues>({});
  const [errors, setErrors] = useState<FieldErrors>({});

  const formQ = useQuery({
    queryKey: ["portal", "process", "ticket", ticketId, "dialog-form", dialogEntityId],
    queryFn: ({ signal }) => portalProcessApi.dialogForm(ticketId, dialogEntityId, signal),
    // options are narrowed per open; never show a stale form
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

  const submitM = useMutation({
    mutationFn: () => {
      if (!form) throw new Error("form not loaded");
      return portalProcessApi.submit(ticketId, {
        activity_dialog_entity_id: dialogEntityId,
        field_values: toSubmitValues(form, values, displayTimeZone()),
      });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["portal", "process", "ticket", ticketId, "state"] });
      await queryClient.invalidateQueries({ queryKey: ["portal", "tickets", ticketId] });
      onClose();
    },
  });

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
    <Dialog open onClose={onClose} title={form?.name ?? t("process.widget.title")} className="max-w-lg">
      {formQ.isLoading ? (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      ) : formQ.isError || !form ? (
        <p className="text-sm text-danger" role="alert" data-testid="portal-process-dialog-load-error">
          {processStartErrorMessage(formQ.error, t, "process.form.loadError")}
        </p>
      ) : (
        <form
          data-testid="portal-process-dialog-form"
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
            testId="portal-process-field"
          />
          {submitM.isError && (
            <p className="text-sm text-danger" role="alert" data-testid="portal-process-error">
              {processStartErrorMessage(submitM.error, t, "process.form.submitError")}
            </p>
          )}
          {form.submit_advice_text && <p className="text-xs text-muted">{form.submit_advice_text}</p>}
          <div className="flex justify-end gap-2 border-t border-hairline pt-3">
            <Button type="button" variant="ghost" onClick={onClose}>
              {t("process.form.cancel")}
            </Button>
            <Button type="submit" variant="primary" disabled={submitM.isPending} data-testid="portal-process-dialog-submit">
              {form.submit_button_text || t("process.form.submit")}
            </Button>
          </div>
        </form>
      )}
    </Dialog>
  );
}
