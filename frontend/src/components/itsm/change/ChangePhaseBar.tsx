import { useTranslation } from "react-i18next";
import { cn } from "@/lib/cn";
import type { ChangePhase, ChangeType } from "@/lib/changeApi";
import { phaseSteps } from "@/lib/changePhases";

/**
 * The path of a change as a row of steps: finished steps, the current one and what follows. The
 * path depends on the type (standard: pre-approved; emergency: no assessment). Display only — the
 * next steps a person may take are buttons of the detail page, driven by `allowed_targets`.
 */
export function ChangePhaseBar({
  changeType,
  phase,
  approvalPending = false,
  className,
}: {
  changeType: ChangeType;
  phase: ChangePhase;
  /** Emergency post-approval that has not been decided yet: "Freigabe" and "Geplant" were skipped. */
  approvalPending?: boolean;
  className?: string;
}) {
  const { t } = useTranslation();
  const steps = phaseSteps(changeType, phase, approvalPending);
  return (
    <ol
      className={cn("flex flex-wrap items-center gap-1 text-xs", className)}
      aria-label={t("itsm.change.phaseBar.label")}
      data-testid="change-phase-bar"
      data-change-type={changeType}
    >
      {steps.map((step) => {
        const off = step.phase === "rejected" || step.phase === "cancelled";
        return (
          <li
            key={step.phase}
            data-testid={`change-phase-${step.phase}`}
            data-state={step.state}
            aria-current={step.state === "current" ? "step" : undefined}
            className={cn(
              "rounded border px-2 py-1 leading-none",
              step.state === "current" && off && step.phase === "rejected" && "border-danger/40 bg-danger/15 font-medium text-danger",
              step.state === "current" && off && step.phase === "cancelled" && "border-hairline bg-surface-subtle font-medium text-muted",
              step.state === "current" && !off && "border-accent/40 bg-accent-dim font-medium text-accent",
              step.state === "done" && "border-hairline bg-surface-subtle text-ink",
              step.state === "upcoming" && "border-hairline text-muted",
            )}
          >
            {t(`itsm.change.phases.${step.phase}`)}
          </li>
        );
      })}
    </ol>
  );
}
