import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ItsmDialogs } from "@/components/itsm/ItsmDialogs";
import { CiStateBadge } from "@/components/itsm/CiStateBadge";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { api, type TicketDetail } from "@/lib/api";
import type { AllowedActions } from "@/lib/aclActions";
import { itsmApi, itsmKeys, type RecentIncident } from "@/lib/itsmApi";
import { ticketPerms } from "@/lib/ticket";

const ROLE_KEYS = new Set(["incident", "major_incident", "service_request", "problem", "change", "change_task"]);

function FloodWarning({ ticketId, recent, canLink }: { ticketId: number; recent: RecentIncident; canLink: boolean }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const link = useMutation({
    mutationFn: () => api.createTicketLink(ticketId, { target_ticket_id: recent.ticket_id, link_type: "Normal" }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["tickets", ticketId] });
    },
  });
  return (
    <p
      className="w-full rounded border border-escalation/30 bg-escalation/10 px-2 py-1 text-xs text-ink"
      role="status"
      data-testid="itsm-flood-warning"
    >
      {t("itsm.zoom.flood", { minutes: recent.minutes_ago, tn: recent.tn })}{" "}
      {link.isSuccess ? (
        <span className="text-muted" data-testid="itsm-flood-linked">
          {t("itsm.zoom.floodLinked")}
        </span>
      ) : (
        canLink && (
          <Button size="sm" disabled={link.isPending} onClick={() => link.mutate()} data-testid="itsm-flood-link">
            {t("itsm.zoom.floodLink")}
          </Button>
        )
      )}
      {link.isError && (
        <span className="text-danger" role="alert" data-testid="itsm-flood-error">
          {" "}
          {t("itsm.zoom.floodError")}
        </span>
      )}
    </p>
  );
}

/**
 * ITSM row of the ticket-zoom header: role, criticality/impact, the matrix priority when it
 * differs from the current one, the service's incident state, the incident-flood warning and the
 * two ITSM field dialogs. Renders nothing unless `GET /tickets/{id}/itsm` says `enabled`.
 */
export function TicketItsmChips({ ticket, allowedActions = null }: { ticket: TicketDetail; allowedActions?: AllowedActions }) {
  const { t } = useTranslation();
  const itsmQ = useQuery({
    queryKey: itsmKeys.ticketItsm(ticket.id),
    queryFn: ({ signal }) => itsmApi.ticketItsm(ticket.id, signal),
    retry: false,
  });
  const itsm = itsmQ.data;
  const matrixId = itsm?.matrix_priority_id ?? null;
  const needPriorities = !!itsm?.enabled && matrixId != null && matrixId !== ticket.priority_id;
  const prioritiesQ = useQuery({
    queryKey: ["reference", "priorities"],
    queryFn: () => api.listReferencePriorities(),
    enabled: needPriorities,
  });
  if (!itsm || !itsm.enabled) return null;

  const matrixName = needPriorities ? (prioritiesQ.data ?? []).find((p) => p.id === matrixId)?.name : undefined;
  const incidentState = itsm.service?.incident_state?.state ?? null;
  const roleLabel = itsm.role && ROLE_KEYS.has(itsm.role) ? t(`itsm.zoom.roles.${itsm.role}`) : null;

  return (
    <div className="flex flex-wrap items-center gap-2 text-xs" data-testid="ticket-itsm-chips">
      {roleLabel && (
        <Badge tone={itsm.role === "major_incident" ? "danger" : "accent"} data-testid="itsm-chip-role">
          {roleLabel}
        </Badge>
      )}
      {itsm.criticality && <Badge data-testid="itsm-chip-criticality">{t("itsm.zoom.criticality", { value: itsm.criticality })}</Badge>}
      {itsm.impact && <Badge data-testid="itsm-chip-impact">{t("itsm.zoom.impact", { value: itsm.impact })}</Badge>}
      {needPriorities && matrixName && (
        <Badge tone="warn" data-testid="itsm-chip-matrix">
          {t("itsm.zoom.matrix", { priority: matrixName })}
        </Badge>
      )}
      {itsm.service && incidentState && (
        <span className="inline-flex items-center gap-1 text-muted" data-testid="itsm-chip-service-state">
          {itsm.service.name}
          <CiStateBadge state={incidentState} />
        </span>
      )}
      <ItsmDialogs ticket={ticket} dialogs={itsm.dialogs} allowedActions={allowedActions} />
      {itsm.recent_incident && (
        <FloodWarning ticketId={ticket.id} recent={itsm.recent_incident} canLink={ticketPerms(ticket).rw} />
      )}
    </div>
  );
}
