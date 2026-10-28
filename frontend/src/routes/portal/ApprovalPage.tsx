import { useId, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { approvalErrorMessage } from "@/lib/approvalsApi";
import { portalApprovalsApi, portalApprovalsKeys } from "@/lib/portalApprovalsApi";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { ApprovalStatusBadge } from "@/components/approvals/ApprovalStatusBadge";
import { APPROVAL_COMMENT_MAX } from "@/components/approvals/DecisionDialog";
import { inputCls } from "@/components/approvals/pickers";

/** One approval of the customer with the decision form; shows no other approvers. */
export function PortalApprovalPage() {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const fieldId = useId();
  const { approvalId } = useParams({ strict: false }) as { approvalId: string };
  const id = Number(approvalId);
  const [comment, setComment] = useState("");
  const [reasonAsked, setReasonAsked] = useState(false);
  const [done, setDone] = useState<"approved" | "rejected" | null>(null);

  const detail = useQuery({
    queryKey: portalApprovalsKeys.detail(id),
    queryFn: ({ signal }) => portalApprovalsApi.get(id, signal),
    enabled: Number.isInteger(id),
    retry: false,
  });
  const decide = useMutation({
    mutationFn: (decision: "approved" | "rejected") =>
      portalApprovalsApi.decide(id, { decision, comment: comment.trim() || null }),
    onSuccess: (fresh, decision) => {
      setDone(decision);
      qc.setQueryData(portalApprovalsKeys.detail(id), fresh);
      void qc.invalidateQueries({ queryKey: portalApprovalsKeys.all() });
    },
  });

  const submit = (decision: "approved" | "rejected") => {
    setReasonAsked(decision === "rejected");
    if (decision === "rejected" && comment.trim() === "") return;
    if (decide.isPending) return;
    decide.mutate(decision);
  };

  const a = detail.data;
  // The server data decides (survives a reload); `done` only adds the thank-you right after voting.
  const mine = a ? (["approved", "rejected"].includes(a.my_decision ?? a.my_status) ? (a.my_decision ?? a.my_status) : null) : null;
  const missing = reasonAsked && comment.trim() === "";
  const unavailable = detail.error instanceof ApiError && detail.error.status === 404;
  return (
    <div className="space-y-4" data-testid="portal-approval-page">
      <Link to="/portal/approvals" className="text-sm text-muted hover:text-ink" data-testid="portal-approval-back">
        ← {t("approvals.portal.back")}
      </Link>
      {detail.isLoading ? (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      ) : detail.isError || !a ? (
        <p className="text-sm text-danger" role="alert" data-testid="portal-approval-error">
          {unavailable ? t("approvals.portal.unavailable") : t("approvals.portal.loadError")}
        </p>
      ) : (
        <>
          <div className="space-y-2">
            <h1 className="break-words font-display text-xl font-semibold text-ink">{a.title}</h1>
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
              <ApprovalStatusBadge status={a.status} />
              {(a.ticket_number || a.ticket_title) && (
                <span data-testid="portal-approval-ticket">
                  {[a.ticket_number ? t("approvals.link.ticket", { number: a.ticket_number }) : null, a.ticket_title]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              )}
            </div>
          </div>
          <section className="space-y-3 rounded-lg border border-hairline bg-surface p-4">
            <p className="text-sm text-ink" data-testid="portal-approval-step">
              {t("approvals.link.step", { name: a.step_name })}
            </p>
            <p className="text-sm text-muted" data-testid="portal-approval-rule">
              {t(a.rule_text_key, { required: a.required ?? undefined })}
              {a.required != null && (
                <> · {t("approvals.steps.progress", { approved: a.approved_count, required: a.required })}</>
              )}
            </p>
            {a.due_at && (
              <p className="text-sm text-muted" data-testid="portal-approval-due">
                {t("approvals.link.due", { date: formatDateTime(a.due_at, i18n.language) })}
              </p>
            )}
            {a.description && <p className="whitespace-pre-wrap text-sm text-ink">{a.description}</p>}
            {(a.describe_lines?.length ?? 0) > 0 && (
              <ul className="space-y-0.5 rounded-md bg-surface-subtle p-3 text-sm text-ink" data-testid="portal-approval-lines">
                {a.describe_lines?.map((line, i) => (
                  <li key={i}>{line}</li>
                ))}
              </ul>
            )}
          </section>
          {mine && (
            <div className="space-y-1" data-testid="portal-approval-mine-box">
              {done && (
                <p className="text-sm font-medium text-green" role="status" data-testid="portal-approval-done">
                  {done === "approved" ? t("approvals.link.doneApproved") : t("approvals.link.doneRejected")}
                </p>
              )}
              <p className="text-sm text-ink" data-testid="portal-approval-mine">
                {t("approvals.agent.myDecision", { decision: t(`approvals.status.${mine}`, { defaultValue: mine }) })}
              </p>
            </div>
          )}
          {mine ? null : a.can_decide ? (
            <section className="space-y-3 rounded-lg border border-hairline bg-surface p-4" data-testid="portal-approval-form">
              <div>
                <label htmlFor={fieldId} className="mb-1 block text-xs font-medium text-muted">
                  {t("approvals.decision.comment")}
                </label>
                <textarea
                  id={fieldId}
                  className={`${inputCls} w-full`}
                  rows={4}
                  maxLength={APPROVAL_COMMENT_MAX}
                  value={comment}
                  onChange={(e) => setComment(e.target.value)}
                  aria-invalid={missing}
                  data-testid="portal-approval-comment"
                />
                <div className="mt-1 flex justify-between gap-2 text-xs">
                  <span className="text-danger" role={missing ? "alert" : undefined} data-testid="portal-approval-comment-error">
                    {missing ? t("approvals.decision.commentMissing") : ""}
                  </span>
                  <span className="text-muted">
                    {comment.length} / {APPROVAL_COMMENT_MAX}
                  </span>
                </div>
              </div>
              {decide.isError && (
                <p className="text-xs text-danger" role="alert" data-testid="portal-approval-decide-error">
                  {approvalErrorMessage(decide.error, t)}
                </p>
              )}
              <div className="flex flex-wrap justify-end gap-2">
                <Button variant="danger" disabled={decide.isPending} onClick={() => submit("rejected")} data-testid="portal-approval-reject">
                  {t("approvals.decision.reject")}
                </Button>
                <Button variant="primary" disabled={decide.isPending} onClick={() => submit("approved")} data-testid="portal-approval-approve">
                  {t("approvals.decision.approve")}
                </Button>
              </div>
            </section>
          ) : (
            <p className="text-sm text-muted" data-testid="portal-approval-cannot">
              {t("approvals.agent.cannotVote")}
            </p>
          )}
        </>
      )}
    </div>
  );
}
