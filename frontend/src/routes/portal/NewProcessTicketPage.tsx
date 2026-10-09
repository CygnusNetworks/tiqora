import { useState } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { portalProcessApi } from "@/lib/portalProcessApi";
import { SelectField } from "@/components/ui/SelectField";
import { Spinner } from "@/components/ui/Spinner";
import { ProcessStartForm } from "@/components/process/ProcessStartForm";
import { processStartErrorMessage } from "@/components/process/processStartError";

export type PortalNewProcessTicketSearch = { process?: string };

/** `/portal/process/new?process=<entity id>`: a customer starts a new ticket in a process. */
export function NewProcessTicketPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const search = useSearch({ from: "/portal/process/new" }) as PortalNewProcessTicketSearch;
  const [picked, setPicked] = useState<string>("");
  const processId = search.process || picked;

  const listQ = useQuery({
    queryKey: ["portal", "process", "startable"],
    queryFn: ({ signal }) => portalProcessApi.listStartable(undefined, signal),
  });

  const createM = useMutation({
    mutationFn: (fieldValues: Record<string, unknown>) =>
      portalProcessApi.createTicket({ process_entity_id: processId, field_values: fieldValues }),
    onSuccess: (out) => {
      void queryClient.invalidateQueries({ queryKey: ["portal", "tickets"] });
      void navigate({ to: "/portal/tickets/$ticketId", params: { ticketId: String(out.ticket_id) } });
    },
  });

  const processes = listQ.data ?? [];
  const known = processes.some((p) => p.entity_id === processId);

  return (
    <div className="space-y-4" data-testid="portal-new-process-page">
      <h1 className="font-display text-xl font-semibold text-ink">{t("portal.process.title")}</h1>

      {listQ.isLoading ? (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      ) : listQ.isError ? (
        <p className="text-sm text-danger" role="alert" data-testid="portal-new-process-error">
          {processStartErrorMessage(listQ.error, t, "portal.process.loadError")}
        </p>
      ) : processes.length === 0 ? (
        <p className="text-sm text-muted" data-testid="portal-new-process-empty">
          {t("portal.process.empty")}
        </p>
      ) : (
        <div className="space-y-4 rounded-lg border border-hairline bg-surface p-4">
          {!search.process && (
            <div>
              <span className="mb-1 block text-xs font-medium uppercase tracking-wide text-muted">
                {t("portal.process.chooseProcess")}
              </span>
              <SelectField
                items={processes.map((p) => ({ value: p.entity_id, label: p.name }))}
                value={picked || null}
                onChange={(v) => {
                  createM.reset();
                  setPicked(v);
                }}
                placeholder="—"
                testId="portal-new-process-select"
              />
            </div>
          )}
          {processId && !known && (
            <p className="text-sm text-danger" role="alert" data-testid="portal-new-process-error">
              {t("process.start.forbidden")}
            </p>
          )}
          {processId && known && (
            <ProcessStartForm
              key={processId}
              api={portalProcessApi}
              processEntityId={processId}
              pending={createM.isPending}
              error={createM.isError ? processStartErrorMessage(createM.error, t, "portal.process.submitError") : null}
              onSubmit={(fv) => createM.mutate(fv)}
              testIdPrefix="portal-new-process"
            />
          )}
        </div>
      )}
    </div>
  );
}
