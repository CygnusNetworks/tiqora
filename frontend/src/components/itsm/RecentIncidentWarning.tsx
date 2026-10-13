import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { itsmApi, itsmKeys } from "@/lib/itsmApi";

/**
 * New-ticket hint: the picked service already had an incident within the flood window.
 * Shows nothing while the ITSM is off, no service is picked or the service is quiet.
 */
export function RecentIncidentWarning({ serviceId, enabled }: { serviceId: number | null; enabled: boolean }) {
  const { t } = useTranslation();
  const q = useQuery({
    queryKey: itsmKeys.recentIncident(serviceId ?? 0, null),
    queryFn: ({ signal }) => itsmApi.recentIncident(serviceId as number, null, signal),
    retry: false,
    enabled: enabled && serviceId != null,
  });
  if (!enabled || serviceId == null || !q.data) return null;
  const r = q.data;
  return (
    <p
      className="flex flex-wrap items-center gap-x-2 rounded-lg border border-escalation/30 bg-escalation/10 px-3 py-2 text-xs text-ink"
      role="status"
      data-testid="new-ticket-recent-incident"
    >
      <span>{t("itsm.newTicket.recentIncident", { minutes: r.minutes_ago, tn: r.tn, title: r.title })}</span>
      <a href={`/agent/tickets/${r.ticket_id}`} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">
        {t("itsm.newTicket.openTicket")}
      </a>
    </p>
  );
}
