import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { portalApi, ApiError } from "@/lib/portalApi";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Dialog } from "@/components/ui/Dialog";
import { Spinner } from "@/components/ui/Spinner";

/**
 * Customer-facing process widget (Znuny CustomerTicketProcess subset).
 * Only dialogs with Interface CustomerInterface appear.
 */
export function PortalProcessWidget({ ticketId }: { ticketId: number }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [startOpen, setStartOpen] = useState(false);
  const [dialogId, setDialogId] = useState<string | null>(null);
  const [fieldValues, setFieldValues] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  const stateQ = useQuery({
    queryKey: ["portal", "process", "ticket", ticketId, "state"],
    queryFn: ({ signal }) => portalApi.portalGetTicketProcessState(ticketId, signal),
  });

  const processesQ = useQuery({
    queryKey: ["portal", "process", "list"],
    queryFn: ({ signal }) => portalApi.portalListProcesses(signal),
    enabled: startOpen,
  });

  const dialogQ = useQuery({
    queryKey: ["portal", "process", "dialog", dialogId],
    queryFn: ({ signal }) => portalApi.portalGetActivityDialog(dialogId!, signal),
    enabled: Boolean(dialogId),
  });

  const startM = useMutation({
    mutationFn: (processEntityId: string) =>
      portalApi.portalStartTicketProcess(ticketId, { process_entity_id: processEntityId }),
    onSuccess: async () => {
      setStartOpen(false);
      setError(null);
      await queryClient.invalidateQueries({
        queryKey: ["portal", "process", "ticket", ticketId, "state"],
      });
      await queryClient.invalidateQueries({ queryKey: ["portal", "tickets", ticketId] });
    },
    onError: (e: unknown) => {
      setError(e instanceof ApiError ? String(e.message) : t("process.widget.loadError"));
    },
  });

  const submitM = useMutation({
    mutationFn: () =>
      portalApi.portalSubmitActivityDialog(ticketId, {
        activity_dialog_entity_id: dialogId!,
        field_values: fieldValues,
      }),
    onSuccess: async () => {
      setDialogId(null);
      setFieldValues({});
      setError(null);
      await queryClient.invalidateQueries({
        queryKey: ["portal", "process", "ticket", ticketId, "state"],
      });
      await queryClient.invalidateQueries({ queryKey: ["portal", "tickets", ticketId] });
    },
    onError: (e: unknown) => {
      setError(e instanceof ApiError ? String(e.message) : t("process.widget.loadError"));
    },
  });

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
        <h2 className="font-display text-sm font-semibold text-ink">
          {t("process.widget.title")}
        </h2>
        {inProcess && state.process_name ? (
          <Badge tone="accent">{state.process_name}</Badge>
        ) : null}
      </div>

      {error && (
        <p className="text-xs text-danger" data-testid="portal-process-error">
          {error}
        </p>
      )}

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
            {t("process.widget.activity")}:{" "}
            <span className="font-medium text-ink">{state.activity_name}</span>
          </p>
          <ul className="flex flex-wrap gap-2">
            {(state.available_dialogs ?? []).map((d) => (
              <li key={d.entity_id}>
                <Button
                  size="sm"
                  variant="primary"
                  onClick={() => {
                    setDialogId(d.entity_id);
                    setFieldValues({});
                    setError(null);
                  }}
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

      <Dialog
        open={startOpen}
        onClose={() => setStartOpen(false)}
        title={t("process.widget.startButton")}
      >
        {processesQ.isLoading ? (
          <Spinner />
        ) : (
          <ul className="max-h-64 space-y-1 overflow-y-auto">
            {(processesQ.data ?? []).map((p) => (
              <li key={p.entity_id}>
                <button
                  type="button"
                  className="w-full rounded px-2 py-1.5 text-left text-sm hover:bg-surface-subtle"
                  onClick={() => startM.mutate(p.entity_id)}
                  disabled={startM.isPending}
                >
                  {p.name}
                </button>
              </li>
            ))}
          </ul>
        )}
      </Dialog>

      <Dialog
        open={Boolean(dialogId)}
        onClose={() => setDialogId(null)}
        title={dialogQ.data?.name ?? t("process.widget.title")}
      >
        {dialogQ.isLoading ? (
          <Spinner />
        ) : dialogQ.data ? (
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              submitM.mutate();
            }}
          >
            {dialogQ.data.field_order.map((fname) => {
              const f = dialogQ.data!.fields[fname];
              const display = String(f?.display ?? "0");
              if (!f || display === "0") return null;
              return (
                <label key={fname} className="block text-sm">
                  <span className="mb-1 block text-xs font-medium uppercase text-muted">
                    {fname}
                    {display === "1" || display === "2" ? " *" : ""}
                  </span>
                  <input
                    className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm"
                    value={fieldValues[fname] ?? String(f.default_value ?? "")}
                    onChange={(ev) =>
                      setFieldValues((v) => ({ ...v, [fname]: ev.target.value }))
                    }
                  />
                </label>
              );
            })}
            <Button type="submit" size="sm" disabled={submitM.isPending}>
              {dialogQ.data.submit_button_text || t("common.submit")}
            </Button>
          </form>
        ) : null}
      </Dialog>
    </div>
  );
}
