import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { portalProcessApi } from "@/lib/portalProcessApi";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Dialog } from "@/components/ui/Dialog";
import { SelectField } from "@/components/ui/SelectField";
import { Spinner } from "@/components/ui/Spinner";
import { ProcessFormBody } from "@/components/process/ProcessFormBody";
import { ProcessStartForm } from "@/components/process/ProcessStartForm";
import { processStartErrorMessage } from "@/components/process/processStartError";

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
  // Not in a process and nothing startable (permission / customer ACL / no process): show nothing.
  if (!inProcess && !state.can_start) return null;

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

  const formQ = useQuery({
    queryKey: ["portal", "process", "ticket", ticketId, "dialog-form", dialogEntityId],
    queryFn: ({ signal }) => portalProcessApi.dialogForm(ticketId, dialogEntityId, signal),
    // options are narrowed per open; never show a stale form
    gcTime: 0,
    retry: false,
  });
  const form = formQ.data;

  const submitM = useMutation({
    mutationFn: (fieldValues: Record<string, unknown>) =>
      portalProcessApi.submit(ticketId, { activity_dialog_entity_id: dialogEntityId, field_values: fieldValues }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["portal", "process", "ticket", ticketId, "state"] });
      await queryClient.invalidateQueries({ queryKey: ["portal", "tickets", ticketId] });
      onClose();
    },
  });

  return (
    <Dialog open onClose={onClose} title={form?.name ?? t("process.widget.title")} className="max-w-lg">
      {formQ.isLoading ? (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      ) : formQ.isError || !form ? (
        <p className="text-sm text-danger" role="alert" data-testid="portal-process-dialog-load-error">
          {processStartErrorMessage(formQ.error, t, "process.form.loadError", "process.dialog.conflict", "process.dialog.forbidden")}
        </p>
      ) : (
        <div className="max-h-[70vh] overflow-y-auto">
          <ProcessFormBody
            form={form}
            pending={submitM.isPending}
            error={
              submitM.isError
                ? processStartErrorMessage(
                    submitM.error,
                    t,
                    "process.form.submitError",
                    "process.dialog.conflict",
                    "process.dialog.forbidden",
                  )
                : null
            }
            onSubmit={(fv) => submitM.mutate(fv)}
            onCancel={onClose}
            testIdPrefix="portal-process-dialog"
          />
        </div>
      )}
    </Dialog>
  );
}
