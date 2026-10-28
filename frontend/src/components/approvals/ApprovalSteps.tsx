import { useTranslation } from "react-i18next";
import { cn } from "@/lib/cn";
import { formatDateTime } from "@/lib/format";
import { approvalRuleKey, type ApprovalApprover, type ApprovalDetail, type ApprovalStep } from "@/lib/approvalsApi";
import { ApprovalStatusBadge } from "./ApprovalStatusBadge";

function groupByStage(steps: ApprovalStep[]): [number, ApprovalStep[]][] {
  const byStage = new Map<number, ApprovalStep[]>();
  for (const s of [...steps].sort((a, b) => a.stage - b.stage || a.position - b.position)) {
    byStage.set(s.stage, [...(byStage.get(s.stage) ?? []), s]);
  }
  return [...byStage.entries()];
}

function ApproverRow({ a }: { a: ApprovalApprover }) {
  const { t } = useTranslation();
  const source = a.source_label ?? t(`approvals.source.${a.source_kind}`, { defaultValue: "" });
  const vote = a.vote;
  return (
    <li className="py-1.5 text-sm" data-testid="approval-approver" data-approver-id={a.id}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-medium text-ink">{a.display_name}</span>
        {source && <span className="text-xs text-muted" data-testid="approval-approver-source">{source}</span>}
        {a.decisive && <span className="text-xs text-muted">{t("approvals.steps.decisive")}</span>}
        {vote ? (
          <ApprovalStatusBadge status={vote.decision} />
        ) : (
          <ApprovalStatusBadge status={a.status} />
        )}
      </div>
      {vote?.on_behalf && (
        <p className="text-xs text-muted" data-testid="approval-on-behalf">
          {t("approvals.steps.onBehalf", { name: vote.decided_by_label })}
        </p>
      )}
      {vote?.comment && (
        <p className="mt-0.5 whitespace-pre-wrap break-words text-xs text-ink" data-testid="approval-vote-comment">
          {vote.comment}
        </p>
      )}
    </li>
  );
}

function StepCard({ step }: { step: ApprovalStep }) {
  const { t, i18n } = useTranslation();
  const required = step.required ?? step.approvers.length;
  return (
    <div className="min-w-0 rounded-md border border-hairline bg-surface p-3" data-testid="approval-step" data-step-id={step.id}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-sm font-semibold text-ink">{step.name}</h4>
        <ApprovalStatusBadge status={step.status} overdue={step.overdue} />
      </div>
      <p className="mt-1 text-xs text-muted" data-testid="approval-step-rule">
        {t(approvalRuleKey(step.rule), { required })}
        {step.required != null && (
          <>
            {" · "}
            <span data-testid="approval-step-progress">
              {t("approvals.steps.progress", { approved: step.approved_count, required: step.required })}
            </span>
          </>
        )}
      </p>
      {step.due_at && (
        <p className={cn("mt-1 text-xs", step.overdue ? "text-danger" : "text-muted")} data-testid="approval-step-due">
          {t("approvals.steps.due", { date: formatDateTime(step.due_at, i18n.language) })}
          {step.overdue && ` · ${t("approvals.overdue")}`}
        </p>
      )}
      <ul className="mt-2 divide-y divide-hairline">
        {step.approvers.map((a) => (
          <ApproverRow key={a.id} a={a} />
        ))}
      </ul>
    </div>
  );
}

/** The steps of an approval grouped by stage; parallel steps of one stage sit side by side from `md` up and stack on phones. */
export function ApprovalSteps({ detail }: { detail: ApprovalDetail }) {
  const { t } = useTranslation();
  return (
    <div className="space-y-4" data-testid="approval-steps">
      {groupByStage(detail.steps).map(([stage, steps]) => (
        <section key={stage} data-testid="approval-stage" data-stage={stage}>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
            {t("approvals.steps.stage", { stage })}
          </h3>
          <div className="grid grid-cols-1 gap-3 md:grid-flow-col md:auto-cols-fr">
            {steps.map((s) => (
              <StepCard key={s.id} step={s} />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
