import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
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
 *
 * When `hideInactiveStart` is set (ticket-zoom ⋮ menu owns the start action),
 * the inactive affordance is suppressed; the dialog can still be opened via
 * controlled `startOpen` / `onStartOpenChange`.
 */
export function ProcessWidget({
  ticketId,
  hideInactiveStart = false,
  startOpen: startOpenProp,
  onStartOpenChange,
}: {
  ticketId: number;
  hideInactiveStart?: boolean;
  startOpen?: boolean;
  onStartOpenChange?: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const [startOpenLocal, setStartOpenLocal] = useState(false);
  const startOpen = startOpenProp ?? startOpenLocal;
  const setStartOpen = (open: boolean) => {
    onStartOpenChange?.(open);
    if (startOpenProp === undefined) setStartOpenLocal(open);
  };
  const [openDialogEntityId, setOpenDialogEntityId] = useState<string | null>(null);

  const stateQ = useQuery({
    queryKey: ["process", "ticket", ticketId, "state"],
    queryFn: ({ signal }) => api.getTicketProcessState(ticketId, signal),
  });

  // Loading/error are transient for a secondary feature — stay out of the way.
  if (stateQ.isLoading) return null;
  if (stateQ.isError || !stateQ.data) {
    return (
      <p className="text-right text-xs text-muted" data-testid="process-widget-error">
        {t("process.widget.loadError")}
      </p>
    );
  }

  const state = stateQ.data;
  const inProcess = Boolean(state.process_entity_id);

  // Not in a process: optional start affordance (or dialog-only when the
  // ticket-zoom ⋮ menu owns the trigger).
  if (!inProcess) {
    return (
      <div
        className={hideInactiveStart ? undefined : "flex justify-end"}
        data-testid="process-widget-inactive"
      >
        {!hideInactiveStart && (
          <button
            type="button"
            onClick={() => setStartOpen(true)}
            className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-muted transition-colors hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
            data-testid="process-widget-start-button"
          >
            <span aria-hidden>＋</span>
            {t("process.widget.startButton")}
          </button>
        )}
        <StartProcessDialog
          ticketId={ticketId}
          open={startOpen}
          onClose={() => setStartOpen(false)}
        />
      </div>
    );
  }

  // Active process: show the informative card.
  return (
    <div
      className="space-y-3 rounded-lg border border-hairline bg-surface p-4"
      data-testid="process-widget"
    >
      <div className="flex items-center justify-between">
        <h2 className="font-display text-sm font-semibold text-ink">
          {t("process.widget.title")}
        </h2>
        <Badge tone="accent">{state.process_name}</Badge>
      </div>

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
