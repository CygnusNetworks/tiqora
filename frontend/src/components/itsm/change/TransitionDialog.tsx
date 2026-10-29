import { useState, type ReactNode } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { changeApi, type Change, type ChangePhase, type ChangeTransitionIn } from "@/lib/changeApi";
import { changeErrorText, FIELD_CLASS, transitionFailures, transitionLabel, type GuardFailure } from "./changeUi";

/** Failures a manager can pass with `override_reason` or `force` (the backend says which via `overridable`). */
const OVERRIDE_REASON = new Set(["block_conflict"]);
const FORCE = new Set(["tasks_open", "approval_pending"]);

export function GuardFailureList({ failures }: { failures: GuardFailure[] }) {
  const { t } = useTranslation();
  return (
    <div className="rounded border border-danger/30 bg-danger/10 p-2 text-xs text-ink" role="alert" data-testid="change-guard-failures">
      <p className="font-medium">{t("itsm.change.transition.refused")}</p>
      <ul className="mt-1 list-disc space-y-0.5 pl-4">
        {failures.map((f) => (
          <li key={f.code} data-testid="change-guard-failure" data-code={f.code}>
            {t([`itsm.change.transition.failures.${f.code}`, "itsm.change.transition.failures.generic"], { code: f.code })}
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * One step of the phase machine (`POST /itsm/changes/{id}/transition`). A 409 `transition_refused`
 * lists every guard that failed; when one of them is `overridable` and the agent is a change
 * manager (`can_manage`, the backend's own check), the dialog offers what passes it: an override
 * reason for a block conflict, "Trotzdem fortfahren" (`force`, with a reason) for open tasks or a
 * cancelled approval. `extra`/`body` carry the fields of special steps (the PIR dialog).
 */
export function TransitionDialog({
  change,
  to,
  onClose,
  onDone,
  title,
  extra,
  body,
  submitDisabled = false,
}: {
  change: Change;
  to: ChangePhase;
  onClose: () => void;
  onDone: (change: Change) => void;
  title?: string;
  extra?: ReactNode;
  body?: Partial<ChangeTransitionIn>;
  submitDisabled?: boolean;
}) {
  const { t } = useTranslation();
  const [reason, setReason] = useState("");
  const [overrideReason, setOverrideReason] = useState("");
  const [force, setForce] = useState(false);

  const run = useMutation({
    mutationFn: () => {
      const payload: ChangeTransitionIn = { to, ...body };
      if (reason.trim()) payload.reason = reason.trim();
      if (overrideReason.trim()) payload.override_reason = overrideReason.trim();
      if (force) payload.force = true;
      return changeApi.transition(change.ticket_id, payload);
    },
    onSuccess: (c) => onDone(c),
  });

  const failures = run.isError ? transitionFailures(run.error) : null;
  const overridable = change.can_manage ? (failures ?? []).filter((f) => f.overridable) : [];
  const offerOverrideReason = overridable.some((f) => OVERRIDE_REASON.has(f.code)) || overrideReason !== "";
  const offerForce = overridable.some((f) => FORCE.has(f.code)) || force;
  const reasonMissing = force && !reason.trim();
  const hint = t(`itsm.change.transition.hints.${change.phase}_${to}`, { defaultValue: "" }) || t(`itsm.change.transition.hints.${to}`, { defaultValue: "" });

  return (
    <Dialog
      open
      onClose={onClose}
      title={title ?? transitionLabel(change.phase, to, t)}
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button
            variant={to === "cancelled" ? "danger" : "primary"}
            size="sm"
            disabled={run.isPending || submitDisabled || reasonMissing}
            onClick={() => run.mutate()}
            data-testid="change-transition-submit"
          >
            {t("itsm.change.transition.submit")}
          </Button>
        </>
      }
    >
      <div className="space-y-3 text-sm" data-testid="change-transition-dialog" data-target={to}>
        {hint && <p className="text-xs text-muted">{hint}</p>}
        {extra}
        <label className="block text-xs text-muted">
          {t("itsm.change.transition.reason")}
          <textarea
            className={`${FIELD_CLASS} mt-1`}
            rows={2}
            maxLength={2000}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            data-testid="change-transition-reason"
          />
        </label>
        {reasonMissing && (
          <p className="text-xs text-danger" data-testid="change-transition-reason-required">
            {t("itsm.change.transition.reasonRequired")}
          </p>
        )}
        {failures && <GuardFailureList failures={failures} />}
        {(offerOverrideReason || offerForce) && (
          <div className="space-y-2 rounded border border-hairline bg-surface-subtle p-2" data-testid="change-transition-override">
            <p className="text-xs text-muted">{t("itsm.change.transition.overrideHint")}</p>
            {offerOverrideReason && (
              <label className="block text-xs text-muted">
                {t("itsm.change.transition.overrideReason")}
                <textarea
                  className={`${FIELD_CLASS} mt-1`}
                  rows={2}
                  maxLength={1000}
                  value={overrideReason}
                  onChange={(e) => setOverrideReason(e.target.value)}
                  data-testid="change-transition-override-reason"
                />
              </label>
            )}
            {offerForce && (
              <label className="flex items-start gap-2 text-xs text-ink">
                <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} data-testid="change-transition-force" />
                <span>{t("itsm.change.transition.force")}</span>
              </label>
            )}
          </div>
        )}
        {run.isError && !failures && (
          <p className="text-xs text-danger" role="alert" data-testid="change-transition-error">
            {changeErrorText(run.error, t)}
          </p>
        )}
      </div>
    </Dialog>
  );
}
