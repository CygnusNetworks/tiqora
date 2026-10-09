import { useState } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { processApi } from "@/lib/processApi";
import { SelectField } from "@/components/ui/SelectField";
import { Spinner } from "@/components/ui/Spinner";
import { ProcessStartForm } from "@/components/process/ProcessStartForm";
import { processStartErrorMessage } from "@/components/process/processStartError";

export type NewProcessTicketSearch = { process?: string };

/** `/agent/process/new?process=<entity id>`: start a new ticket in a process through its start dialog. */
export function NewProcessTicketPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const search = useSearch({ from: "/agent/process/new" }) as NewProcessTicketSearch;
  const [picked, setPicked] = useState<string>("");
  const processId = search.process || picked;

  const listQ = useQuery({
    queryKey: ["process", "startable"],
    queryFn: ({ signal }) => processApi.listStartable(undefined, signal),
  });

  const createM = useMutation({
    mutationFn: (fieldValues: Record<string, unknown>) =>
      processApi.createTicket({ process_entity_id: processId, field_values: fieldValues }),
    onSuccess: (out) => {
      void queryClient.invalidateQueries({ queryKey: ["tickets"] });
      void navigate({ to: "/agent/tickets/$ticketId", params: { ticketId: String(out.ticket_id) } });
    },
  });

  const processes = listQ.data ?? [];
  const known = processes.some((p) => p.entity_id === processId);

  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-6" data-testid="new-process-page">
      <h1 className="font-display text-xl font-semibold text-ink">{t("process.newTicket.title")}</h1>

      <div className="mt-5">
        {listQ.isLoading ? (
          <div className="flex justify-center py-8">
            <Spinner />
          </div>
        ) : listQ.isError ? (
          <p className="text-sm text-danger" data-testid="new-process-error">
            {t("process.start.loadError")}
          </p>
        ) : processes.length === 0 ? (
          <p className="text-sm text-muted" data-testid="new-process-empty">
            {t("process.newTicket.empty")}
          </p>
        ) : (
          <div className="space-y-4">
            {!search.process && (
              <div>
                <span className="mb-1 block text-xs font-medium uppercase tracking-wide text-muted">
                  {t("process.newTicket.chooseProcess")}
                </span>
                <SelectField
                  items={processes.map((p) => ({ value: p.entity_id, label: p.name }))}
                  value={picked || null}
                  onChange={(v) => {
                    createM.reset();
                    setPicked(v);
                  }}
                  placeholder="—"
                  testId="new-process-select"
                />
              </div>
            )}
            {processId && !known && (
              <p className="text-sm text-danger" data-testid="new-process-error">
                {t("process.start.forbidden")}
              </p>
            )}
            {processId && known && (
              <ProcessStartForm
                key={processId}
                processEntityId={processId}
                pending={createM.isPending}
                error={createM.isError ? processStartErrorMessage(createM.error, t, "process.form.submitError") : null}
                onSubmit={(fv) => createM.mutate(fv)}
                testIdPrefix="new-process"
              />
            )}
          </div>
        )}
      </div>
    </div>
  );
}
