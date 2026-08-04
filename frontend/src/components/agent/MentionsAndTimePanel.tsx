import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/Button";

/**
 * Compact ticket-zoom panel for Znuny-compatible mentions + time accounting.
 * Tables are shared with Znuny (``mention``, ``time_accounting``).
 */
export function MentionsAndTimePanel({ ticketId }: { ticketId: number }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [units, setUnits] = useState("1");
  const [mentionUserId, setMentionUserId] = useState("");

  const mentionsQ = useQuery({
    queryKey: ["tickets", ticketId, "mentions"],
    queryFn: () => api.listTicketMentions(ticketId),
  });
  const timeQ = useQuery({
    queryKey: ["tickets", ticketId, "time-accounting"],
    queryFn: () => api.listTicketTimeAccounting(ticketId),
  });
  const agentsQ = useQuery({
    queryKey: ["reference", "agents"],
    queryFn: () => api.listReferenceAgents(),
  });

  const bookM = useMutation({
    mutationFn: () =>
      api.createTicketTimeAccounting(ticketId, { time_unit: Number(units) || 1 }),
    onSuccess: () => {
      setUnits("1");
      void qc.invalidateQueries({ queryKey: ["tickets", ticketId, "time-accounting"] });
    },
  });
  const mentionM = useMutation({
    mutationFn: () =>
      api.createTicketMention(ticketId, { user_id: Number(mentionUserId) }),
    onSuccess: () => {
      setMentionUserId("");
      void qc.invalidateQueries({ queryKey: ["tickets", ticketId, "mentions"] });
    },
  });
  const delTimeM = useMutation({
    mutationFn: (id: number) => api.deleteTicketTimeAccounting(ticketId, id),
    onSuccess: () =>
      void qc.invalidateQueries({ queryKey: ["tickets", ticketId, "time-accounting"] }),
  });
  const delMentionM = useMutation({
    mutationFn: (id: number) => api.deleteTicketMention(ticketId, id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["tickets", ticketId, "mentions"] }),
  });

  const totalUnits = (timeQ.data ?? []).reduce((s, r) => s + Number(r.time_unit), 0);

  return (
    <div
      className="grid gap-4 rounded-lg border border-line bg-surface p-3 md:grid-cols-2"
      data-testid="mentions-time-panel"
    >
      <section>
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
          {t("ticket.mentions")}
        </h3>
        <ul className="mb-2 space-y-1 text-sm">
          {(mentionsQ.data ?? []).map((m) => (
            <li key={m.id} className="flex items-center justify-between gap-2">
              <span>{m.user_name || m.user_login || m.user_id}</span>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => delMentionM.mutate(m.id)}
              >
                ×
              </Button>
            </li>
          ))}
          {(mentionsQ.data ?? []).length === 0 && (
            <li className="text-xs text-muted">—</li>
          )}
        </ul>
        <div className="flex gap-2">
          <select
            className="min-w-0 flex-1 rounded-md border border-line bg-surface px-2 py-1 text-xs"
            value={mentionUserId}
            onChange={(e) => setMentionUserId(e.target.value)}
          >
            <option value="">{t("ticket.addMention")}</option>
            {(agentsQ.data ?? []).map((a) => (
              <option key={a.id} value={a.id}>
                {a.full_name} ({a.login})
              </option>
            ))}
          </select>
          <Button
            type="button"
            size="sm"
            disabled={!mentionUserId || mentionM.isPending}
            onClick={() => mentionM.mutate()}
          >
            +
          </Button>
        </div>
      </section>

      <section>
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
          {t("ticket.timeAccounting")} · {totalUnits}
        </h3>
        <ul className="mb-2 space-y-1 text-sm">
          {(timeQ.data ?? []).map((r) => (
            <li key={r.id} className="flex items-center justify-between gap-2">
              <span>
                {r.time_unit} · {r.create_by_login || r.create_by}
              </span>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => delTimeM.mutate(r.id)}
              >
                ×
              </Button>
            </li>
          ))}
          {(timeQ.data ?? []).length === 0 && <li className="text-xs text-muted">—</li>}
        </ul>
        <div className="flex gap-2">
          <input
            className="w-20 rounded-md border border-line bg-surface px-2 py-1 text-xs"
            type="number"
            min="0.01"
            step="0.25"
            value={units}
            onChange={(e) => setUnits(e.target.value)}
            aria-label={t("ticket.timeUnits")}
          />
          <Button
            type="button"
            size="sm"
            disabled={bookM.isPending}
            onClick={() => bookM.mutate()}
          >
            {t("ticket.addTime")}
          </Button>
        </div>
      </section>
    </div>
  );
}
