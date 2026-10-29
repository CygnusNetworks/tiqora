import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/lib/api";
import { logoUrl } from "@/lib/assets";
import { approvalErrorCode, approvalErrorMessage, approvalRuleKey } from "@/lib/approvalsApi";
import { approvalLinkApi, type ApprovalLinkDecision, type ApprovalLinkInspect } from "@/lib/approvalLinkApi";
import { formatDateTime } from "@/lib/format";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { DecisionCommentField } from "@/components/approvals/DecisionCommentField";

type Phase =
  | { kind: "loading" }
  | { kind: "missing" }
  | { kind: "ready"; info: ApprovalLinkInspect }
  | { kind: "error"; message: string; retryable: boolean }
  | { kind: "done"; result: ApprovalLinkDecision };

/** Codes that end the page: a dead link, or an approver who may not decide any more. Retrying cannot change them. */
const LINK_CODES = new Set([
  "link_invalid",
  "link_expired",
  "link_used",
  "approver_invalid",
  "customer_approver_outside_company",
]);

/**
 * Public confirm page for the decision links of approval e-mails (`/approve#t=<token>`).
 *
 * The token lives only in the URL fragment: it is read once, the fragment is removed from the
 * address bar right away, and from then on the token exists only in this component's memory and in
 * the POST bodies of `approvalLinkApi` — never in a query string, storage or log. No session is
 * needed (or used) here; `goLogin` in `lib/api` ignores 401s on `/approve`.
 */
export function ApprovalLinkPage() {
  const { t, i18n } = useTranslation();
  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const token = useRef<string | null>(null);
  // StrictMode runs the effect twice with refs kept: the second pass must neither re-read the
  // (already removed) fragment nor call inspect again.
  const started = useRef(false);

  const [comment, setComment] = useState("");
  const [reasonAsked, setReasonAsked] = useState(false);
  const [pending, setPending] = useState<"approved" | "rejected" | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  // Rate limit on decide: the buttons stay off until this moment (epoch ms).
  const [blockedUntil, setBlockedUntil] = useState(0);
  const [now, setNow] = useState(() => Date.now());

  // Seconds the server asked us to wait (429); the buttons and the retry stay off until then.
  const holdOff = (err: unknown) => {
    if (err instanceof ApiError && err.status === 429 && err.retryAfterSeconds) {
      setBlockedUntil(Date.now() + err.retryAfterSeconds * 1000);
      setNow(Date.now());
    }
  };

  // A dead link will not get better by asking again; a throttle, 5xx or network error might.
  const failure = (err: unknown): Phase => {
    const code = approvalErrorCode(err);
    holdOff(err);
    return { kind: "error", message: approvalErrorMessage(err, t), retryable: !(code && LINK_CODES.has(code)) };
  };

  const runInspect = (tok: string) => {
    setPhase({ kind: "loading" });
    approvalLinkApi.inspect(tok).then(
      (info) => {
        if (info.valid) {
          setPhase({ kind: "ready", info });
        } else {
          const code = info.code && LINK_CODES.has(info.code) ? info.code : "link_invalid";
          setPhase({ kind: "error", message: t(`approvals.errors.${code}`), retryable: false });
        }
      },
      (err: unknown) => setPhase(failure(err)),
    );
  };

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const found = new URLSearchParams(window.location.hash.replace(/^#/, "")).get("t");
    window.history.replaceState(window.history.state, "", "/approve");
    if (!found) {
      setPhase({ kind: "missing" });
      return;
    }
    token.current = found;
    runInspect(found);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- runs once; t/failure only build messages
  }, []);

  useEffect(() => {
    if (blockedUntil <= Date.now()) return;
    const id = window.setInterval(() => {
      setNow(Date.now());
      if (Date.now() >= blockedUntil) window.clearInterval(id);
    }, 1000);
    return () => window.clearInterval(id);
  }, [blockedUntil]);

  const submit = async (decision: "approved" | "rejected") => {
    if (!token.current || pending || Date.now() < blockedUntil) return;
    setReasonAsked(decision === "rejected");
    if (decision === "rejected" && comment.trim() === "") return;
    setPending(decision);
    setFormError(null);
    try {
      const result = await approvalLinkApi.decide(token.current, decision, comment.trim());
      setPhase({ kind: "done", result });
    } catch (err) {
      const code = approvalErrorCode(err);
      if (code && LINK_CODES.has(code)) {
        setPhase(failure(err));
      } else {
        setFormError(approvalErrorMessage(err, t));
        holdOff(err);
      }
    } finally {
      setPending(null);
    }
  };

  const shell = (children: React.ReactNode) => (
    <div className="flex min-h-screen items-center justify-center bg-bg p-4">
      <div
        className="w-full max-w-md space-y-4 rounded-lg border border-hairline bg-surface p-6"
        data-testid="approval-link-page"
      >
        <img src={logoUrl} alt="" width={32} height={32} className="rounded" />
        <h1 className="font-display text-xl font-semibold text-ink">{t("approvals.link.title")}</h1>
        {children}
      </div>
    </div>
  );

  if (phase.kind === "loading") {
    return shell(
      <div className="flex justify-center py-4" role="status" aria-label={t("approvals.link.loading")}>
        <Spinner />
      </div>,
    );
  }
  if (phase.kind === "missing") {
    return shell(
      <div className="space-y-1" role="alert" data-testid="approval-link-error">
        <p className="text-sm text-danger">{t("approvals.link.incomplete")}</p>
        <p className="text-xs text-muted">{t("approvals.link.reopen")}</p>
      </div>,
    );
  }
  const waitSeconds = Math.max(0, Math.ceil((blockedUntil - now) / 1000));
  if (phase.kind === "error") {
    return shell(
      <div className="space-y-2" data-testid="approval-link-error">
        <div className="space-y-1" role="alert">
          <p className="text-sm text-danger">{phase.message}</p>
          {phase.retryable && <p className="text-xs text-muted">{t("approvals.link.reopen")}</p>}
          {waitSeconds > 0 && (
            <p className="text-xs text-muted">{t("approvals.link.retryIn", { seconds: waitSeconds })}</p>
          )}
        </div>
        {phase.retryable && token.current && (
          <Button
            variant="secondary"
            disabled={waitSeconds > 0}
            onClick={() => token.current && runInspect(token.current)}
            data-testid="approval-link-retry"
          >
            {t("approvals.link.retry")}
          </Button>
        )}
      </div>,
    );
  }
  if (phase.kind === "done") {
    const { decision, status } = phase.result;
    return shell(
      <div className="space-y-2" data-testid="approval-link-done">
        <p className="text-sm font-medium text-green">
          {decision === "approved" ? t("approvals.link.doneApproved") : t("approvals.link.doneRejected")}
        </p>
        <p className="text-sm text-ink" data-testid="approval-link-status">
          {t("approvals.link.requestStatus", { status: t(`approvals.status.${status}`, { defaultValue: status }) })}
        </p>
        <p className="text-xs text-muted">{t("approvals.link.closeHint")}</p>
      </div>,
    );
  }

  const { info } = phase;
  const locked = pending !== null || waitSeconds > 0;
  const ruleText = info.rule
    ? t(approvalRuleKey(info.rule), { required: info.required ?? undefined })
    : null;
  return shell(
    <div className="space-y-4">
      <div className="space-y-1">
        {info.title && (
          <p className="text-base font-medium text-ink" data-testid="approval-link-request-title">
            {info.title}
          </p>
        )}
        {(info.ticket_number || info.ticket_title) && (
          <p className="text-sm text-muted" data-testid="approval-link-ticket">
            {[info.ticket_number ? t("approvals.link.ticket", { number: info.ticket_number }) : null, info.ticket_title]
              .filter(Boolean)
              .join(" · ")}
          </p>
        )}
        {info.step_name && (
          <p className="text-sm text-ink" data-testid="approval-link-step">
            {t("approvals.link.step", { name: info.step_name })}
          </p>
        )}
        {ruleText && (
          <p className="text-sm text-muted" data-testid="approval-link-rule">
            {ruleText}
          </p>
        )}
        {info.due_at && (
          <p className="text-sm text-muted" data-testid="approval-link-due">
            {t("approvals.link.due", { date: formatDateTime(info.due_at, i18n.language) })}
          </p>
        )}
      </div>
      {info.description && <p className="whitespace-pre-wrap text-sm text-ink">{info.description}</p>}
      {(info.describe_lines?.length ?? 0) > 0 && (
        <ul className="space-y-0.5 rounded-md bg-surface-subtle p-3 text-sm text-ink" data-testid="approval-link-lines">
          {info.describe_lines?.map((line, i) => (
            <li key={i}>{line}</li>
          ))}
        </ul>
      )}
      {info.approver_name && (
        <p className="text-sm font-medium text-ink" data-testid="approval-link-approver">
          {t("approvals.link.decidingAs", { name: info.approver_name })}
        </p>
      )}
      <DecisionCommentField
        value={comment}
        onChange={setComment}
        decision={reasonAsked ? "rejected" : null}
        showError={reasonAsked}
        testIdPrefix="approval-link"
      />
      {formError && (
        <div className="space-y-1" role="alert" data-testid="approval-link-form-error">
          <p className="text-xs text-danger">{formError}</p>
          {waitSeconds > 0 && (
            <p className="text-xs text-muted">{t("approvals.link.retryIn", { seconds: waitSeconds })}</p>
          )}
        </div>
      )}
      <div className="flex flex-wrap justify-end gap-2">
        <Button variant="danger" onClick={() => void submit("rejected")} disabled={locked} data-testid="approval-link-reject">
          {t("approvals.decision.reject")}
        </Button>
        <Button variant="primary" onClick={() => void submit("approved")} disabled={locked} data-testid="approval-link-approve">
          {t("approvals.decision.approve")}
        </Button>
      </div>
    </div>,
  );
}
