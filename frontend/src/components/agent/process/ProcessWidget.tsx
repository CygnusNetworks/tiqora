import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Spinner } from "@/components/ui/Spinner";
import { StartProcessDialog } from "./StartProcessDialog";
import { ActivityDialogModal } from "./ActivityDialogModal";

/**
 * ProcessManagement (BPM) widget for the ticket zoom page.
 *
 * Always mounted (unlike a tab that's conditionally rendered) — it fetches
 * ``GET /api/v1/process/ticket/{id}/state`` itself and switches between two
 * views: the current activity + its available dialogs when the ticket *is*
 * in a process, or a "start process" affordance (embedded dialog, see
 * StartProcessDialog.tsx) when it is not.
 */
export function ProcessWidget({ ticketId }: { ticketId: number }) {
  const { t } = useTranslation();
  const [startOpen, setStartOpen] = useState(false);
  const [openDialogEntityId, setOpenDialogEntityId] = useState<string | null>(null);

  const stateQ = useQuery({
    queryKey: ["process", "ticket", ticketId, "state"],
    queryFn: ({ signal }) => api.getTicketProcessState(ticketId, signal),
  });

  if (stateQ.isLoading) {
    return (
      <div
        className="flex items-center justify-center rounded-lg border border-hairline bg-surface p-4"
        data-testid="process-widget-loading"
      >
        <Spinner />
      </div>
    );
  }

  if (stateQ.isError || !stateQ.data) {
    return (
      <div
        className="rounded-lg border border-hairline bg-surface p-4 text-sm text-danger"
        data-testid="process-widget-error"
      >
        {t("process.widget.loadError")}
      </div>
    );
  }

  const state = stateQ.data;
  const inProcess = Boolean(state.process_entity_id);

  return (
    <div
      className="space-y-3 rounded-lg border border-hairline bg-surface p-4"
      data-testid="process-widget"
    >
      <div className="flex items-center justify-between">
        <h2 className="font-display text-sm font-semibold text-ink">
          {t("process.widget.title")}
        </h2>
        {inProcess && <Badge tone="accent">{state.process_name}</Badge>}
      </div>

      {inProcess ? (
        <div className="space-y-3" data-testid="process-widget-active">
          <div>
            <span className="text-xs uppercase tracking-wide text-muted">
              {t("process.widget.activity")}
            </span>
            <p className="font-medium text-ink" data-testid="process-widget-activity-name">
              {state.activity_name}
            </p>
          </div>

          <div>
            <span className="mb-1 block text-xs uppercase tracking-wide text-muted">
              {t("process.widget.actions")}
            </span>
            {state.available_dialogs.length === 0 ? (
              <p className="text-sm text-muted">{t("process.widget.noDialogs")}</p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {state.available_dialogs.map((d) => (
                  <Button
                    key={d.entity_id}
                    variant="secondary"
                    size="sm"
                    onClick={() => setOpenDialogEntityId(d.entity_id)}
                    data-testid={`process-dialog-button-${d.entity_id}`}
                    title={d.description_short || undefined}
                  >
                    {d.name}
                  </Button>
                ))}
              </div>
            )}
          </div>
        </div>
      ) : (
        <div className="space-y-2" data-testid="process-widget-inactive">
          <p className="text-sm text-muted">{t("process.widget.notInProcess")}</p>
          <Button
            variant="primary"
            size="sm"
            onClick={() => setStartOpen(true)}
            data-testid="process-widget-start-button"
          >
            {t("process.widget.startButton")}
          </Button>
        </div>
      )}

      <StartProcessDialog ticketId={ticketId} open={startOpen} onClose={() => setStartOpen(false)} />
      {openDialogEntityId && (
        <ActivityDialogModal
          ticketId={ticketId}
          activityDialogEntityId={openDialogEntityId}
          open={openDialogEntityId !== null}
          onClose={() => setOpenDialogEntityId(null)}
        />
      )}
    </div>
  );
}
