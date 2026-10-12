import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { processApi } from "@/lib/processApi";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { SelectField } from "@/components/ui/SelectField";
import { Spinner } from "@/components/ui/Spinner";
import { ProcessStartForm } from "@/components/process/ProcessStartForm";
import { processStartErrorMessage } from "@/components/process/processStartError";

const labelClass = "mb-1 block text-xs font-medium uppercase tracking-wide text-muted";

/**
 * Enroll an existing ticket into a process, in two steps: pick the process, then fill its start
 * dialog (`startForm` with the ticket, ACL filtered) and `enroll`. The server runs the same
 * start check as for a new process ticket; a 403/409 is shown as a plain German message.
 */
export function StartProcessDialog({
  ticketId,
  open,
  onClose,
}: {
  ticketId: number;
  open: boolean;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<string>("");
  const [step, setStep] = useState<"pick" | "form">("pick");

  const listQ = useQuery({
    queryKey: ["process", "startable", ticketId],
    queryFn: ({ signal }) => processApi.listStartable({ ticketId }, signal),
    enabled: open,
  });

  const startM = useMutation({
    mutationFn: (fieldValues: Record<string, unknown>) =>
      processApi.enroll(ticketId, { process_entity_id: selected, field_values: fieldValues }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["process", "ticket", ticketId, "state"] });
      void queryClient.invalidateQueries({ queryKey: ["process", "ticket", ticketId, "path"] });
      void queryClient.invalidateQueries({ queryKey: ["tickets", ticketId] });
      onClose();
    },
  });

  if (!open) return null;

  return (
    <Dialog open={open} onClose={onClose} title={t("process.start.title")} className="max-w-lg">
      {listQ.isLoading ? (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      ) : listQ.isError ? (
        <p className="text-sm text-danger" data-testid="process-start-load-error">
          {t("process.start.loadError")}
        </p>
      ) : !listQ.data || listQ.data.length === 0 ? (
        <p className="text-sm text-muted" data-testid="process-start-empty">
          {t("process.start.empty")}
        </p>
      ) : step === "pick" ? (
        <form
          data-testid="process-start-pick"
          onSubmit={(e) => {
            e.preventDefault();
            if (selected) setStep("form");
          }}
          className="space-y-3"
        >
          <div>
            <span className={labelClass}>{t("process.start.selectLabel")}</span>
            <SelectField
              items={listQ.data.map((p) => ({ value: p.entity_id, label: p.name }))}
              value={selected || null}
              onChange={setSelected}
              placeholder="—"
              testId="process-start-select"
            />
          </div>
          <div className="flex justify-end gap-2 border-t border-hairline pt-3">
            <Button type="button" variant="ghost" onClick={onClose}>
              {t("process.start.cancel")}
            </Button>
            <Button type="submit" variant="primary" disabled={!selected} data-testid="process-start-next">
              {t("process.start.next")}
            </Button>
          </div>
        </form>
      ) : (
        <div className="max-h-[70vh] overflow-y-auto">
          <ProcessStartForm
            key={selected}
            processEntityId={selected}
            ticketId={ticketId}
            pending={startM.isPending}
            error={startM.isError ? processStartErrorMessage(startM.error, t, "process.start.startError") : null}
            onSubmit={(fv) => startM.mutate(fv)}
            onCancel={() => {
              startM.reset();
              setStep("pick");
            }}
            testIdPrefix="process-start"
          />
        </div>
      )}
    </Dialog>
  );
}
