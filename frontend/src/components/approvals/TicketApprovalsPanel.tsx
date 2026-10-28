import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { ticketPerms } from "@/lib/ticket";
import {
  approvalErrorMessage,
  approvalsApi,
  approvalsKeys,
  type ApprovalDecision,
  type ApprovalDetail,
  type ApprovalSummary,
} from "@/lib/approvalsApi";
import { ApprovalAuditList } from "./ApprovalAuditList";
import { ApprovalStatusBadge } from "./ApprovalStatusBadge";
import { ApprovalSteps } from "./ApprovalSteps";
import { DecisionDialog } from "./DecisionDialog";
import { ForwardDialog } from "./ForwardDialog";
import { pickMyRow } from "./myRow";
import { RequestApprovalDialog } from "./RequestApprovalDialog";
import { WithdrawConfirm } from "./WithdrawConfirm";

export type TicketApprovalsFilter = { subjectType?: string; approvalKey?: string };

function ApprovalItem({ summary, onChanged }: { summary: ApprovalSummary; onChanged: () => void }) {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [decision, setDecision] = useState<ApprovalDecision | null>(null);
  const [forwarding, setForwarding] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const pending = summary.status === "pending";

  // The row to vote with (and the refresh/forward flags) live in the detail; load it once it is needed.
  const detailQ = useQuery({
    queryKey: approvalsKeys.detail(summary.id),
    queryFn: ({ signal }) => approvalsApi.get(summary.id, signal),
    enabled: open || (pending && summary.my_pending),
    retry: false,
  });
  const detail = detailQ.data;
  const row = pickMyRow(detail?.my_rows);

  const changed = (d: ApprovalDetail) => {
    setDecision(null);
    setForwarding(false);
    setConfirmCancel(false);
    qc.setQueryData(approvalsKeys.detail(d.id), d);
    onChanged();
  };
  const cancel = useMutation({ mutationFn: () => approvalsApi.cancel(summary.id), onSuccess: changed });
  const refresh = useMutation({ mutationFn: () => approvalsApi.refreshApprovers(summary.id), onSuccess: changed });

  return (
    <li className="rounded-md border border-hairline bg-surface p-3" data-testid="ticket-approval" data-approval-id={summary.id}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="min-w-0 break-words text-sm font-medium text-ink" data-testid="ticket-approval-title">
          {summary.title}
        </span>
        <ApprovalStatusBadge status={summary.status} />
      </div>
      <p className="mt-1 text-xs text-muted" data-testid="ticket-approval-meta">
        {t("approvals.ticket.requestedBy", { name: summary.requester_label })}
        {" · "}
        {formatDateTime(summary.created_at, i18n.language)}
        {" · "}
        {pending && summary.current_stage != null
          ? t("approvals.ticket.stage", { stage: summary.current_stage })
          : t("approvals.ticket.noStage")}
      </p>
      {pending && summary.my_pending && row?.on_behalf_of_label && (
        <p className="mt-1 text-xs text-muted" data-testid="ticket-approval-on-behalf">
          {t("approvals.agent.onBehalfOf", { name: row.on_behalf_of_label })}
        </p>
      )}
      <div className="mt-2 flex flex-wrap gap-2">
        {pending && summary.my_pending && row && (
          <>
            <Button size="sm" variant="primary" onClick={() => setDecision("approved")} data-testid="ticket-approval-approve">
              {t("approvals.decision.approve")}
            </Button>
            <Button size="sm" variant="danger" onClick={() => setDecision("rejected")} data-testid="ticket-approval-reject">
              {t("approvals.decision.reject")}
            </Button>
            {row.can_forward && (
              <Button size="sm" onClick={() => setForwarding(true)} data-testid="ticket-approval-forward">
                {t("approvals.forward.submit")}
              </Button>
            )}
          </>
        )}
        <Button size="sm" onClick={() => setOpen((o) => !o)} aria-expanded={open} data-testid="ticket-approval-details">
          {open ? t("approvals.ticket.hideDetails") : t("approvals.ticket.details")}
        </Button>
        {pending && summary.can_cancel && (
          <Button size="sm" onClick={() => setConfirmCancel(true)} data-testid="ticket-approval-cancel">
            {t("approvals.withdraw.action")}
          </Button>
        )}
        {pending && detail?.can_refresh && (
          <Button
            size="sm"
            disabled={refresh.isPending}
            onClick={() => refresh.mutate()}
            data-testid="ticket-approval-refresh"
          >
            {t("approvals.ticket.refresh")}
          </Button>
        )}
      </div>
      {refresh.isError && (
        <p className="mt-1 text-xs text-danger" role="alert" data-testid="ticket-approval-error">
          {approvalErrorMessage(refresh.error, t)}
        </p>
      )}
      {open && (
        <div className="mt-3 space-y-3" data-testid="ticket-approval-body">
          {detailQ.isError && (
            <p className="text-xs text-danger" role="alert">
              {approvalErrorMessage(detailQ.error, t)}
            </p>
          )}
          {!detail && !detailQ.isError && <p className="text-xs text-muted">{t("common.loading")}</p>}
          {detail && (
            <>
              {detail.description && (
                <p className="whitespace-pre-wrap break-words text-sm text-ink">{detail.description}</p>
              )}
              <ApprovalSteps detail={detail} />
              <ApprovalAuditList events={detail.events} />
            </>
          )}
        </div>
      )}
      {row && (
        <>
          <DecisionDialog
            open={decision !== null}
            onClose={() => setDecision(null)}
            approvalId={summary.id}
            approverId={row.approver_id}
            initialDecision={decision ?? "approved"}
            onDone={changed}
          />
          <ForwardDialog
            open={forwarding}
            onClose={() => setForwarding(false)}
            approvalId={summary.id}
            approverId={row.approver_id}
            onDone={changed}
          />
        </>
      )}
      <WithdrawConfirm
        open={confirmCancel}
        title={summary.title}
        pending={cancel.isPending}
        error={cancel.error}
        onConfirm={() => cancel.mutate()}
        onCancel={() => {
          cancel.reset();
          setConfirmCancel(false);
        }}
      />
    </li>
  );
}

/**
 * Approvals of one ticket (zoom page, and itsm-change's page with a `filter`). Newest first; withdrawn
 * ones sit under "Ältere Freigaben". Hidden while there is nothing to show and the agent cannot request.
 */
export function TicketApprovalsPanel({
  ticketId,
  filter,
  title,
}: {
  ticketId: number;
  filter?: TicketApprovalsFilter;
  title?: string;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [requesting, setRequesting] = useState(false);
  const [olderOpen, setOlderOpen] = useState(false);

  // Same key as the zoom page's ticket query, so this shares its cache entry.
  const ticketQ = useQuery({ queryKey: ["tickets", ticketId], queryFn: () => api.getTicket(ticketId) });
  // A filtered panel is embedded by a feature that owns requesting (e.g. the change page's CAB approval): the
  // request dialog here creates a plain ticket approval the filter would hide, so it offers no request button.
  const canRequest = ticketQ.data && !filter ? ticketPerms(ticketQ.data).rw : false;
  const q = useQuery({
    queryKey: approvalsKeys.ticket(ticketId),
    queryFn: ({ signal }) => approvalsApi.ticketApprovals(ticketId, {}, signal),
    retry: false,
  });

  const changed = () => {
    void qc.invalidateQueries({ queryKey: approvalsKeys.ticket(ticketId) });
    void qc.invalidateQueries({ queryKey: approvalsKeys.all() });
  };

  const all = (q.data ?? [])
    .filter((a) => !filter?.subjectType || a.subject_type === filter.subjectType)
    .filter((a) => !filter?.approvalKey || a.approval_key === filter.approvalKey)
    .sort((a, b) => b.created_at.localeCompare(a.created_at) || b.id - a.id);
  const current = all.filter((a) => a.status !== "cancelled");
  const older = all.filter((a) => a.status === "cancelled");

  if (all.length === 0 && !canRequest && !q.isError) return null;

  return (
    <section
      className="space-y-3 rounded-md border border-hairline bg-surface p-4"
      aria-label={title ?? t("approvals.ticket.title")}
      data-testid="ticket-approvals-panel"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-ink">{title ?? t("approvals.ticket.title")}</h2>
        {canRequest && (
          <Button size="sm" onClick={() => setRequesting(true)} data-testid="ticket-approvals-request">
            {t("approvals.ticket.request")}
          </Button>
        )}
      </div>
      {q.isError && (
        <p className="text-xs text-danger" role="alert" data-testid="ticket-approvals-error">
          {t("approvals.ticket.loadFailed")}
        </p>
      )}
      {current.length > 0 && (
        <ul className="space-y-2" data-testid="ticket-approvals-list">
          {current.map((a) => (
            <ApprovalItem key={a.id} summary={a} onChanged={changed} />
          ))}
        </ul>
      )}
      {older.length > 0 && (
        <div>
          <Button
            size="sm"
            variant="ghost"
            aria-expanded={olderOpen}
            onClick={() => setOlderOpen((o) => !o)}
            data-testid="ticket-approvals-older-toggle"
          >
            {t("approvals.ticket.older", { count: older.length })}
          </Button>
          {olderOpen && (
            <ul className="mt-2 space-y-2" data-testid="ticket-approvals-older">
              {older.map((a) => (
                <ApprovalItem key={a.id} summary={a} onChanged={changed} />
              ))}
            </ul>
          )}
        </div>
      )}
      {canRequest && (
        <RequestApprovalDialog
          open={requesting}
          onClose={() => setRequesting(false)}
          ticketId={ticketId}
          ticketTitle={ticketQ.data?.title ?? ""}
          onDone={() => {
            setRequesting(false);
            changed();
          }}
        />
      )}
    </section>
  );
}
