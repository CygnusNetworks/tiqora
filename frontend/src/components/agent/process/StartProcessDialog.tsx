import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, ApiError } from "@/lib/api";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";

const inputClass =
  "w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent focus:border-accent";
const labelClass = "mb-1 block text-xs font-medium uppercase tracking-wide text-muted";

/**
 * Embedded "start a process" affordance for a ticket, per the ProcessManagement
 * subtask 4 task notes: the frontend has no standalone ``/agent/process/start``
 * route — a modal launched from ``ProcessWidget`` is the simpler, equally
 * valid interpretation, mirroring how the calendar's ``AppointmentDialog`` is
 * an embedded modal rather than a dedicated route.
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

  const listQ = useQuery({
    queryKey: ["process", "list"],
    queryFn: ({ signal }) => api.listProcesses(signal),
    enabled: open,
  });

  const startM = useMutation({
    mutationFn: (processEntityId: string) =>
      api.startTicketProcess(ticketId, { process_entity_id: processEntityId }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["process", "ticket", ticketId, "state"] });
      onClose();
    },
  });

  if (!open) return null;

  return (
    <Dialog open={open} onClose={onClose} title={t("process.start.title")} className="max-w-md">
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
      ) : (
        <form
          data-testid="process-start-form"
          onSubmit={(e) => {
            e.preventDefault();
            if (!selected) return;
            startM.mutate(selected);
          }}
          className="space-y-3"
        >
          <div>
            <span className={labelClass}>{t("process.start.selectLabel")}</span>
            <select
              className={inputClass}
              value={selected}
              onChange={(e) => setSelected(e.target.value)}
              data-testid="process-start-select"
              required
            >
              <option value="" disabled>
                —
              </option>
              {listQ.data.map((p) => (
                <option key={p.entity_id} value={p.entity_id}>
                  {p.name}
                </option>
              ))}
            </select>
          </div>

          {startM.isError && (
            <p className="text-sm text-danger" data-testid="process-start-error">
              {startM.error instanceof ApiError ? startM.error.message : t("process.start.startError")}
            </p>
          )}

          <div className="flex justify-end gap-2 border-t border-hairline pt-3">
            <Button type="button" variant="ghost" onClick={onClose}>
              {t("process.start.cancel")}
            </Button>
            <Button
              type="submit"
              variant="primary"
              disabled={!selected || startM.isPending}
              data-testid="process-start-submit"
            >
              {t("process.start.submit")}
            </Button>
          </div>
        </form>
      )}
    </Dialog>
  );
}
