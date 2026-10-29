import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { RouterAnchor } from "@/components/ui/RouterAnchor";
import { SelectField } from "@/components/ui/SelectField";
import { Spinner } from "@/components/ui/Spinner";
import type { TicketDetail } from "@/lib/api";
import { changeApi, changeKeys, type ChangeType } from "@/lib/changeApi";
import { CHANGE_TYPES } from "@/lib/changePhases";
import { changeDetailHref } from "@/lib/itsmRoutes";
import { itsmApi, itsmKeys, type TicketItsm } from "@/lib/itsmApi";
import { ticketPerms } from "@/lib/ticket";
import { ChangeRecord } from "./ChangeRecord";
import { ChangeTypeBadge } from "./ChangeTypeBadge";
import { RiskBadge } from "./RiskBadge";
import { changeErrorText, legacyChangesHref, SECTION_CLASS, useChangeRefresh, windowText } from "./changeUi";

const PANEL_CLASS = "min-w-0 space-y-3 rounded-lg border border-hairline bg-surface p-3 text-sm";

function ModeAHint({ ticketId }: { ticketId: number }) {
  const { t } = useTranslation();
  return (
    <section className={PANEL_CLASS} aria-label={t("itsm.change.zoom.title")} data-testid="change-mode-a-hint">
      <h2 className="text-sm font-medium text-ink">{t("itsm.change.zoom.title")}</h2>
      <p className="text-xs text-muted">{t("itsm.change.zoom.modeA")}</p>
      <RouterAnchor href={legacyChangesHref(ticketId)} className="text-xs text-accent hover:underline" data-testid="change-mode-a-link">
        {t("itsm.change.zoom.modeALink")}
      </RouterAnchor>
    </section>
  );
}

function TaskCard({ task }: { task: NonNullable<TicketItsm["change_task"]> }) {
  const { t } = useTranslation();
  const slot = windowText(task.planned_start, task.planned_end);
  return (
    <section className={SECTION_CLASS} aria-label={t("itsm.change.zoom.taskTitle")} data-testid="change-task-card">
      <h2 className="text-sm font-medium text-ink">{t("itsm.change.zoom.taskTitle")}</h2>
      <p className="flex flex-wrap items-center gap-2 text-xs">
        <span className="text-muted">{t("itsm.change.zoom.taskOf")}</span>
        {task.change_tn ? (
          <RouterAnchor href={changeDetailHref(task.change_ticket_id)} className="text-accent hover:underline" data-testid="change-task-parent">
            {task.change_tn}
          </RouterAnchor>
        ) : (
          <span className="text-muted">{t("itsm.change.zoom.taskParentHidden")}</span>
        )}
      </p>
      <p className="flex flex-wrap items-center gap-2 text-xs text-muted">
        <Badge tone="muted">{t(`itsm.change.list.kinds.${task.task_kind}`, { defaultValue: task.task_kind })}</Badge>
        <span>{t("itsm.change.zoom.taskStep", { position: task.position })}</span>
        {slot && <span>{slot}</span>}
      </p>
    </section>
  );
}

function AdoptCard({ ticket }: { ticket: TicketDetail }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const refresh = useChangeRefresh(ticket.id);
  const [type, setType] = useState<ChangeType>("normal");
  const adopt = useMutation({
    mutationFn: () => changeApi.adopt(ticket.id, type),
    onSuccess: (c) => {
      void qc.invalidateQueries({ queryKey: itsmKeys.ticketItsm(ticket.id) });
      refresh(c);
    },
  });
  const canWrite = ticketPerms(ticket).rw;
  return (
    <section className={PANEL_CLASS} aria-label={t("itsm.change.zoom.title")} data-testid="change-adopt">
      <h2 className="text-sm font-medium text-ink">{t("itsm.change.zoom.title")}</h2>
      <p className="text-xs text-muted">{t("itsm.change.zoom.adoptHint")}</p>
      {canWrite && (
        <div className="flex flex-wrap items-center gap-2">
          <div className="w-48">
            <SelectField<ChangeType>
              testId="change-adopt-type"
              aria-label={t("itsm.change.create.type")}
              items={CHANGE_TYPES.map((ct) => ({ value: ct, label: t(`itsm.change.types.${ct}`) }))}
              value={type}
              onChange={setType}
            />
          </div>
          <Button size="sm" variant="primary" disabled={adopt.isPending} onClick={() => adopt.mutate()} data-testid="change-adopt-submit">
            {t("itsm.change.zoom.adopt")}
          </Button>
        </div>
      )}
      {adopt.isError && (
        <p className="text-xs text-danger" role="alert" data-testid="change-adopt-error">
          {changeErrorText(adopt.error, t)}
        </p>
      )}
    </section>
  );
}

function RecordPanel({ ticketId, embedApprovals }: { ticketId: number; embedApprovals: boolean }) {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: changeKeys.detail(ticketId), queryFn: ({ signal }) => changeApi.get(ticketId, signal), retry: false });
  const change = q.data;
  return (
    <section className={PANEL_CLASS} aria-label={t("itsm.change.zoom.title")} data-testid="change-panel">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="flex flex-wrap items-center gap-2 text-sm font-medium text-ink">
          {t("itsm.change.zoom.title")}
          {change && <ChangeTypeBadge type={change.change_type} />}
          {change && <RiskBadge level={change.effective_risk_level} />}
        </h2>
        <RouterAnchor href={changeDetailHref(ticketId)} className="text-xs text-accent hover:underline" data-testid="change-panel-open-page">
          {t("itsm.change.zoom.openPage")}
        </RouterAnchor>
      </div>
      {q.isLoading && <Spinner />}
      {q.isError && (
        <p className="text-xs text-danger" role="alert" data-testid="change-panel-error">
          {changeErrorText(q.error, t)}
        </p>
      )}
      {change && <ChangeRecord change={change} embedApprovals={embedApprovals} />}
    </section>
  );
}

/**
 * Change management in the ticket zoom (after the approvals panel). A change ticket shows its
 * record (or, without one, "Als Change führen"); a change task shows its change, kind and window;
 * in Mode A (Znuny's change management) a hint points to the read-only Znuny changes. Renders
 * nothing for other tickets or while ITSM is off. `embedApprovals` is false in the zoom: its
 * approvals panel already lists the CAB request.
 */
export function ChangePanel({ ticket, embedApprovals }: { ticket: TicketDetail; embedApprovals: boolean }) {
  const itsmQ = useQuery({
    queryKey: itsmKeys.ticketItsm(ticket.id),
    queryFn: ({ signal }) => itsmApi.ticketItsm(ticket.id, signal),
    retry: false,
  });
  const itsm = itsmQ.data;
  if (!itsm?.enabled) return null;
  if (itsm.mode === "znuny_readthrough") return itsm.role === "change" ? <ModeAHint ticketId={ticket.id} /> : null;
  if (itsm.mode !== "tiqora") return null;
  if (itsm.role === "change_task" && itsm.change_task) return <TaskCard task={itsm.change_task} />;
  if (itsm.role !== "change") return null;
  if (!itsm.change) return <AdoptCard ticket={ticket} />;
  return <RecordPanel ticketId={ticket.id} embedApprovals={embedApprovals} />;
}
