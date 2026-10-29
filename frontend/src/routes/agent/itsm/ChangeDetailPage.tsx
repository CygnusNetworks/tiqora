import { useQuery } from "@tanstack/react-query";
import { useParams } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { ChangeEventsSection } from "@/components/itsm/change/ChangeEventsSection";
import { ChangeRecord } from "@/components/itsm/change/ChangeRecord";
import { PortalVisibleSwitch } from "@/components/itsm/change/PortalVisibleSwitch";
import { ChangeTypeBadge } from "@/components/itsm/change/ChangeTypeBadge";
import { RiskBadge } from "@/components/itsm/change/RiskBadge";
import { changeErrorText, legacyChangesHref } from "@/components/itsm/change/changeUi";
import { RouterAnchor } from "@/components/ui/RouterAnchor";
import { Spinner } from "@/components/ui/Spinner";
import { changeApi, changeErrorCode, changeKeys } from "@/lib/changeApi";

/**
 * `/agent/itsm/changes/$ticketId`: one change, full width: number, title, type and risk, a link to
 * its ticket and the portal switch, then the record (phase bar, plans, approval with voting, risk,
 * impact, conflicts, tasks) and the change's own history. In Mode A it points to the read-only
 * Znuny changes of the ticket.
 */
export function ChangeDetailPage() {
  const { t } = useTranslation();
  const { ticketId: raw } = useParams({ from: "/agent/itsm/changes/$ticketId" });
  const ticketId = Number(raw);
  const valid = Number.isInteger(ticketId) && ticketId > 0;
  const q = useQuery({
    queryKey: changeKeys.detail(ticketId),
    queryFn: ({ signal }) => changeApi.get(ticketId, signal),
    enabled: valid,
    retry: false,
  });
  const change = q.data;
  const code = q.isError ? changeErrorCode(q.error) : null;

  return (
    <div className="mx-auto w-full max-w-6xl space-y-4 px-4 py-4" data-testid="changeDetailPage">
      <RouterAnchor href="/agent/itsm/changes" className="text-xs text-accent hover:underline">
        {t("itsm.change.detail.back")}
      </RouterAnchor>
      {!valid || q.isError ? (
        <div className="space-y-2 rounded-md border border-hairline bg-surface p-4 text-sm" role="alert" data-testid="change-detail-error">
          <p className="text-danger">{valid ? changeErrorText(q.error, t) : t("itsm.change.errors.change_not_found")}</p>
          {code === "change_mode_a" && (
            <RouterAnchor href={legacyChangesHref(ticketId)} className="text-accent hover:underline" data-testid="change-detail-legacy">
              {t("itsm.change.zoom.modeALink")}
            </RouterAnchor>
          )}
        </div>
      ) : !change ? (
        <div className="flex justify-center py-16">
          <Spinner />
        </div>
      ) : (
        <>
          <header className="min-w-0 space-y-1" data-testid="change-detail-header">
            <p className="font-mono text-xs text-muted">{change.tn}</p>
            <h1 className="break-words text-lg font-semibold text-ink">{change.title}</h1>
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <ChangeTypeBadge type={change.change_type} />
              <RiskBadge level={change.effective_risk_level} />
              <span className="text-muted">{change.ticket_state}</span>
              <RouterAnchor href={`/agent/tickets/${change.ticket_id}`} className="text-accent hover:underline" data-testid="change-detail-ticket">
                {t("itsm.change.detail.openTicket")}
              </RouterAnchor>
            </div>
            <PortalVisibleSwitch change={change} />
          </header>
          <ChangeRecord change={change} embedApprovals wide />
          <ChangeEventsSection ticketId={change.ticket_id} />
        </>
      )}
    </div>
  );
}
