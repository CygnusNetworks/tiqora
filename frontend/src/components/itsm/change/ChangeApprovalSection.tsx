import { useMutation, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ApprovalStatusBadge } from "@/components/approvals/ApprovalStatusBadge";
import { TicketApprovalsPanel } from "@/components/approvals/TicketApprovalsPanel";
import { SelectField } from "@/components/ui/SelectField";
import { Spinner } from "@/components/ui/Spinner";
import { changeApi, changeKeys, type Change, type ChangeApprovalSnapshot } from "@/lib/changeApi";
import { changeErrorText, SECTION_CLASS, useChangeRefresh, utcText } from "./changeUi";

/** The engine's view of a CAB approval: subject type and key of the change port (approvals contract). */
const CHANGE_APPROVAL_FILTER = { subjectType: "itsm.change", approvalKey: "change_cab" } as const;
/** Phases in which the CAB can still be chosen (not while it decides, not after the change is scheduled). */
const CAB_PHASES: ReadonlySet<Change["phase"]> = new Set(["draft", "submitted", "assessment", "rejected"]);
const DEFAULT_CAB = 0;

function Snapshot({ snapshot }: { snapshot: ChangeApprovalSnapshot }) {
  const { t } = useTranslation();
  return (
    <div className="space-y-1 text-xs" data-testid="change-approval-snapshot" data-status={snapshot.status}>
      <p className="flex flex-wrap items-center gap-2">
        <ApprovalStatusBadge status={snapshot.status} />
        <span className="text-ink">{t("itsm.change.approval.round", { round: snapshot.round })}</span>
        {snapshot.votes_total != null && snapshot.votes_approved != null && (
          <span className="text-muted">{t("itsm.change.approval.votes", { approved: snapshot.votes_approved, count: snapshot.votes_total })}</span>
        )}
        {snapshot.votes_rejected ? <span className="text-muted">{t("itsm.change.approval.rejections", { count: snapshot.votes_rejected })}</span> : null}
      </p>
      <p className="text-muted">
        {t("itsm.change.approval.requestedAt", { time: utcText(snapshot.created_at) })}
        {snapshot.decided_at && ` · ${t("itsm.change.approval.decidedAt", { time: utcText(snapshot.decided_at) })}`}
      </p>
      {snapshot.reason === "no_approvers" && (
        <p className="text-danger" data-testid="change-approval-no-approvers">
          {t("itsm.change.approval.noApprovers")}
        </p>
      )}
    </div>
  );
}

/**
 * "Freigabe (CAB)": the CAB the request goes to (choosable until the request), the snapshot of the
 * current round from the approval engine, and how the change got its approval (CAB, standard
 * template, low risk, after the fact). The request itself is the step "Freigabe anfordern" of the
 * phase bar. On the change page (`embedApprovals`) the engine's approval panel is embedded for
 * voting; in the ticket zoom only the snapshot shows, because the zoom lists the approval itself.
 */
export function ChangeApprovalSection({ change, embedApprovals }: { change: Change; embedApprovals: boolean }) {
  const { t } = useTranslation();
  const id = change.ticket_id;
  const refresh = useChangeRefresh(id);
  const q = useQuery({ queryKey: changeKeys.approval(id), queryFn: ({ signal }) => changeApi.approval(id, signal) });
  const canChooseCab = change.can_write && CAB_PHASES.has(change.phase);
  const cabsQ = useQuery({ queryKey: changeKeys.cabs(), queryFn: ({ signal }) => changeApi.cabs(signal), enabled: canChooseCab });
  const setCab = useMutation({ mutationFn: (cabId: number | null) => changeApi.update(id, { cab_id: cabId }), onSuccess: refresh });

  const approval = q.data;
  // GDPR erasure may remove the engine's record of a round; the change keeps its approval_id (N6)
  const missing = !!approval && (approval.history_missing || (approval.approval_id != null && approval.snapshot == null));
  const via = approval?.approval_via && approval.approval_via !== "cab" ? approval.approval_via : null;

  return (
    <section className={SECTION_CLASS} aria-label={t("itsm.change.approval.title")} data-testid="change-approval">
      <h3 className="text-sm font-semibold text-ink">{t("itsm.change.approval.title")}</h3>
      {canChooseCab && (
        <div className="max-w-sm text-xs text-muted">
          <span>{t("itsm.change.approval.cab")}</span>
          <SelectField<number>
            testId="change-approval-cab"
            aria-label={t("itsm.change.approval.cab")}
            className="mt-1"
            disabled={setCab.isPending || !cabsQ.data}
            items={[
              { value: DEFAULT_CAB, label: t("itsm.change.approval.defaultCab") },
              ...(cabsQ.data ?? []).map((c) => ({
                value: c.id,
                label: c.name,
                hint: c.is_emergency ? t("itsm.change.approval.emergencyCab") : undefined,
              })),
            ]}
            value={change.cab_id ?? DEFAULT_CAB}
            onChange={(v) => setCab.mutate(v === DEFAULT_CAB ? null : v)}
          />
        </div>
      )}
      {(q.error ?? setCab.error) && (
        <p className="text-xs text-danger" role="alert" data-testid="change-approval-error">
          {changeErrorText(q.error ?? setCab.error, t)}
        </p>
      )}
      {q.isLoading && <Spinner />}
      {approval && (
        <>
          {via && (
            <p className="text-xs text-ink" data-testid="change-approval-via">
              {t(`itsm.change.approval.via.${via}`, { defaultValue: via })}
              {approval.approved_at && ` · ${utcText(approval.approved_at)}`}
            </p>
          )}
          {missing ? (
            <p className="text-xs text-muted" data-testid="change-approval-history-missing">
              {t("itsm.change.approval.historyMissing")}
            </p>
          ) : approval.snapshot ? (
            <Snapshot snapshot={approval.snapshot} />
          ) : (
            !via && (
              <p className="text-xs text-muted" data-testid="change-approval-none">
                {t("itsm.change.approval.none")}
              </p>
            )
          )}
          {approval.snapshot && !missing && !embedApprovals && (
            <p className="text-xs text-muted" data-testid="change-approval-vote-hint">
              {t("itsm.change.approval.voteHint")}
            </p>
          )}
        </>
      )}
      {embedApprovals && approval?.approval_id != null && !missing && (
        <TicketApprovalsPanel ticketId={id} filter={CHANGE_APPROVAL_FILTER} title={t("itsm.change.approval.panelTitle")} />
      )}
    </section>
  );
}
