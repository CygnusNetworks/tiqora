import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/cn";
import type { Change, ChangePhase } from "@/lib/changeApi";
import { ChangeApprovalSection } from "./ChangeApprovalSection";
import { ChangeImpactSection } from "./ChangeImpactSection";
import { ChangePhaseBar } from "./ChangePhaseBar";
import { ChangePlansSection } from "./ChangePlansSection";
import { ChangeRiskSection } from "./ChangeRiskSection";
import { ChangeTasksSection } from "./ChangeTasksSection";
import { ConflictList } from "./ConflictList";
import { PirDialog } from "./PirDialog";
import { TransitionDialog } from "./TransitionDialog";
import { transitionLabel, useChangeRefresh } from "./changeUi";

/** Dynamic-field drift (the Znuny fields were edited behind Tiqora's back); `ticket_closed` has its own banner. */
const DRIFT_FIELDS = new Set(["type_field", "phase_field"]);

function Banners({ change }: { change: Change }) {
  const { t } = useTranslation();
  const drift = change.drift.filter((d) => DRIFT_FIELDS.has(d));
  return (
    <>
      {change.drift.includes("ticket_closed") && (
        <p className="rounded border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-ink" role="status" data-testid="change-ticket-closed-banner">
          {t("itsm.change.banners.ticketClosed", { phase: t(`itsm.change.phases.${change.phase}`), state: change.ticket_state })}
        </p>
      )}
      {drift.length > 0 && (
        <p className="rounded border border-escalation/30 bg-escalation/10 px-3 py-2 text-xs text-ink" role="status" data-testid="change-drift-banner">
          {t("itsm.change.banners.drift", { fields: drift.map((d) => t(`itsm.change.banners.fields.${d}`)).join(", ") })}
        </p>
      )}
      {change.process_ticket && (
        <p className="rounded border border-hairline bg-surface-subtle px-3 py-2 text-xs text-muted" data-testid="change-process-banner">
          {t("itsm.change.banners.processTicket")}
        </p>
      )}
    </>
  );
}

/**
 * Everything of one change record: banners, the phase bar with the steps the agent may take
 * (`allowed_targets`), then plans, approval, risk, impact, conflicts and tasks. Shared by the
 * change page (full width, `embedApprovals`) and the ticket zoom (`embedApprovals={false}`: the
 * zoom's own approvals panel lists the CAB request, so it is not shown twice).
 */
export function ChangeRecord({ change, embedApprovals, wide = false }: { change: Change; embedApprovals: boolean; wide?: boolean }) {
  const { t } = useTranslation();
  const refresh = useChangeRefresh(change.ticket_id);
  const [target, setTarget] = useState<ChangePhase | null>(null);
  const done = (c: Change) => {
    setTarget(null);
    refresh(c);
  };
  const targets = change.can_write ? change.allowed_targets : [];

  return (
    <div className="min-w-0 space-y-3" data-testid="change-record" data-phase={change.phase}>
      <Banners change={change} />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <ChangePhaseBar changeType={change.change_type} phase={change.phase} />
        {targets.length > 0 && (
          <div className="flex flex-wrap gap-2" data-testid="change-actions">
            {targets.map((to) => (
              <Button
                key={to}
                size="sm"
                variant={to === "cancelled" ? "ghost" : "secondary"}
                onClick={() => setTarget(to)}
                data-target={to}
                data-testid={`change-action-${to}`}
              >
                {transitionLabel(change.phase, to, t)}
              </Button>
            ))}
          </div>
        )}
      </div>
      <div className={cn("grid gap-3", wide && "lg:grid-cols-2")}>
        <ChangePlansSection change={change} />
        <ChangeApprovalSection change={change} embedApprovals={embedApprovals} />
        <ChangeRiskSection change={change} />
        <ChangeImpactSection change={change} />
        <ConflictList ticketId={change.ticket_id} />
        <ChangeTasksSection change={change} />
      </div>
      {target === "review" || target === "closed" ? (
        <PirDialog change={change} to={target} onClose={() => setTarget(null)} onDone={done} />
      ) : (
        target && <TransitionDialog change={change} to={target} onClose={() => setTarget(null)} onDone={done} />
      )}
    </div>
  );
}
