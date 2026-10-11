import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { processApi } from "@/lib/processApi";
import { Dialog } from "@/components/ui/Dialog";
import { Spinner } from "@/components/ui/Spinner";
import { ProcessFormBody } from "@/components/process/ProcessFormBody";
import { processStartErrorMessage } from "@/components/process/processStartError";

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

  const formQ = useQuery({
    queryKey: ["process", "ticket", ticketId, "dialog-form", activityDialogEntityId],
    queryFn: ({ signal }) => processApi.dialogForm(ticketId, activityDialogEntityId, signal),
    enabled: open,
    // the options are ACL-filtered per open; never show a stale form
    gcTime: 0,
    retry: false,
  });
  const form = formQ.data;

  const submitM = useMutation({
    mutationFn: (fieldValues: Record<string, unknown>) =>
      processApi.submit(ticketId, { activity_dialog_entity_id: activityDialogEntityId, field_values: fieldValues }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["process", "ticket", ticketId, "state"] });
      // Everything under ["tickets", id] (ticket, acl-actions, scoped owner/responsible options): a dialog can
      // move the queue or change the state.
      void queryClient.invalidateQueries({ queryKey: ["tickets", ticketId] });
      onClose();
    },
  });

  if (!open) return null;

  return (
    <Dialog open={open} onClose={onClose} title={form?.name ?? t("process.widget.title")} className="max-w-lg">
      {formQ.isLoading ? (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      ) : formQ.isError || !form ? (
        <p className="text-sm text-danger" role="alert" data-testid="process-dialog-load-error">
          {processStartErrorMessage(formQ.error, t, "process.form.loadError", "process.dialog.conflict", "process.dialog.forbidden")}
        </p>
      ) : (
        <div className="max-h-[70vh] overflow-y-auto">
          <ProcessFormBody
            key={form.activity_dialog_entity_id}
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
            testIdPrefix="process-dialog"
            fieldTestId="process-field"
            optionsTarget={{ scope: "agent", kind: "dialog", ticketId, dialogEntityId: activityDialogEntityId }}
          />
        </div>
      )}
    </Dialog>
  );
}
