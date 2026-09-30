import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import type { DynamicFieldDef } from "@/lib/phoneApi";
import { elapsedToMinutes, formatElapsed } from "@/lib/phoneCall";
import type { PhoneTicketFieldsValue } from "./phoneTicketValue";
import { SelectMenu, type SelectMenuItem } from "@/components/ui/SelectMenu";
import { ChevronDownIcon } from "@/components/ui/icons";
import { cn } from "@/lib/cn";
import { ComposerTimeChip } from "../ComposerTimeChip";
import { AttachmentChips } from "../telegram/ComposerChips";
import type { ChatAttachment } from "../telegram/useChatAttachments";
import { DynamicFieldInputs } from "./DynamicFieldInputs";
import { PendingTimeInput } from "./PendingTimeInput";

const SELECT_TRIGGER_CLASS =
  "flex w-full items-center justify-between gap-2 rounded-md border border-hairline bg-surface-subtle px-3 py-2 text-left text-[13.5px] text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";

function Picker<T extends number>({
  label,
  items,
  value,
  onSelect,
  onClear,
  testId,
  noneLabel,
}: {
  label: string;
  items: SelectMenuItem<T>[];
  value: T | null;
  onSelect: (v: T) => void;
  onClear?: () => void;
  testId: string;
  noneLabel: string;
}) {
  return (
    <label className="block">
      <span className="mb-1 flex items-center justify-between text-[12px] font-medium text-muted">
        {label}
        {onClear && value !== null && (
          <button type="button" className="text-[11px] hover:text-ink" onClick={onClear} data-testid={`${testId}-clear`}>
            ×
          </button>
        )}
      </span>
      <SelectMenu
        items={items}
        value={value ?? undefined}
        onSelect={onSelect}
        placeholder={noneLabel}
        panelTestId={`${testId}-panel`}
        trigger={({ open, ref, toggleProps }) => (
          <button ref={ref} type="button" data-testid={testId} {...toggleProps} className={SELECT_TRIGGER_CLASS}>
            <span className="min-w-0 flex-1 truncate">
              {items.find((i) => i.value === value)?.label ?? noneLabel}
            </span>
            <ChevronDownIcon className={cn("shrink-0 text-muted transition-transform duration-150", open && "rotate-180")} />
          </button>
        )}
      />
    </label>
  );
}

/**
 * The AgentTicketPhone parity fields of the New-ticket page's phone mode
 * (the owner sits in `TicketPropsBar`): responsible, type, service, SLA, the pending time of a pending
 * initial state, the booked time (timer from page open), dynamic fields and
 * attachments. Controlled — the page owns the value and submits it.
 */
export function PhoneTicketFields({
  value,
  onChange,
  pendingState,
  elapsed,
  timerRunning,
  onToggleTimer,
  fields,
  attachments,
  onAttach,
  onRemoveAttachment,
}: {
  value: PhoneTicketFieldsValue;
  onChange: (next: PhoneTicketFieldsValue) => void;
  /** The chosen initial state is a pending one — ask for the time. */
  pendingState: boolean;
  elapsed: number;
  timerRunning: boolean;
  onToggleTimer: () => void;
  fields: DynamicFieldDef[];
  attachments: ChatAttachment[];
  onAttach: (files: FileList) => void;
  onRemoveAttachment: (id: number) => void;
}) {
  const { t } = useTranslation();
  const set = (patch: Partial<PhoneTicketFieldsValue>) => onChange({ ...value, ...patch });

  const agentsQ = useQuery({ queryKey: ["reference", "agents"], queryFn: () => api.listReferenceAgents() });
  const typesQ = useQuery({ queryKey: ["reference", "types"], queryFn: () => api.listReferenceTypes() });
  const servicesQ = useQuery({ queryKey: ["reference", "services"], queryFn: () => api.listReferenceServices() });
  const slasQ = useQuery({
    queryKey: ["reference", "slas", value.serviceId],
    queryFn: () => api.listReferenceSlas(value.serviceId ? { service_id: value.serviceId } : {}),
  });
  const agentItems = (agentsQ.data ?? []).map((a) => ({ value: a.id, label: a.full_name || a.login, hint: a.login }));
  const typeItems = (typesQ.data ?? []).map((x) => ({ value: x.id, label: x.name }));
  const serviceItems = (servicesQ.data ?? []).map((x) => ({ value: x.id, label: x.name }));
  const slaItems = (slasQ.data ?? []).map((x) => ({ value: x.id, label: x.name }));
  const none = t("admin.form.selectPlaceholder");

  // The time chip keeps its own text: re-key it for each new timer minute,
  // but only while the agent has not typed a value.
  const autoMinutes = elapsedToMinutes(elapsed);
  const [chipKey, setChipKey] = useState(0);
  useEffect(() => {
    if (value.timeUnits === null) setChipKey(autoMinutes);
  }, [autoMinutes, value.timeUnits]);

  return (
    <div className="space-y-4" data-testid="phone-ticket-fields">
      <div className="grid gap-4 sm:grid-cols-2">
        <Picker
          label={t("ticket.toolbar.responsible")}
          items={agentItems}
          value={value.responsibleId}
          onSelect={(v) => set({ responsibleId: v })}
          onClear={() => set({ responsibleId: null })}
          testId="new-ticket-responsible"
          noneLabel={none}
        />
        {typeItems.length > 1 && (
          <Picker label={t("ticket.type")} items={typeItems} value={value.typeId} onSelect={(v) => set({ typeId: v })} testId="new-ticket-type" noneLabel={none} />
        )}
        {serviceItems.length > 0 && (
          <Picker
            label={t("ticket.service")}
            items={serviceItems}
            value={value.serviceId}
            onSelect={(v) => set({ serviceId: v, slaId: null })}
            onClear={() => set({ serviceId: null, slaId: null })}
            testId="new-ticket-service"
            noneLabel={none}
          />
        )}
        {slaItems.length > 0 && (
          <Picker
            label={t("ticket.sla")}
            items={slaItems}
            value={value.slaId}
            onSelect={(v) => set({ slaId: v })}
            onClear={() => set({ slaId: null })}
            testId="new-ticket-sla"
            noneLabel={none}
          />
        )}
      </div>

      {pendingState && (
        <div className="flex flex-wrap items-center gap-2 text-xs" data-testid="new-ticket-pending">
          <span className="text-muted">{t("phone.pendingUntil")}</span>
          <PendingTimeInput value={value.pendingAt} onChange={(v) => set({ pendingAt: v })} testId="new-ticket-pending-time" />
        </div>
      )}

      <DynamicFieldInputs
        fields={fields}
        values={value.dfValues}
        onChange={(name, values) => set({ dfValues: { ...value.dfValues, [name]: values } })}
        testId="new-ticket-df"
      />

      <div className="flex flex-wrap items-center gap-3 text-xs">
        <span className="inline-flex items-center gap-2 rounded-md border border-hairline bg-surface px-2 py-1" title={t("phone.timer")}>
          <span aria-hidden className={cn("h-2 w-2 rounded-full", timerRunning ? "animate-pulse bg-danger" : "bg-muted")} />
          <span className="font-mono tabular-nums text-ink" data-testid="new-ticket-timer">
            {formatElapsed(elapsed)}
          </span>
          <button type="button" onClick={onToggleTimer} data-testid="new-ticket-timer-toggle" className="text-muted hover:text-ink">
            {timerRunning ? t("phone.pause") : t("phone.resume")}
          </button>
        </span>
        <ComposerTimeChip
          key={chipKey}
          value={value.timeUnits ?? (autoMinutes > 0 ? String(autoMinutes) : "")}
          onChange={(v) => set({ timeUnits: v })}
          testId="new-ticket-time"
        />
        <label className="cursor-pointer rounded border border-hairline px-2 py-0.5 text-muted hover:text-ink">
          📎 {t("phone.attach")}
          <input
            type="file"
            multiple
            hidden
            data-testid="new-ticket-attach-input"
            onChange={(e) => {
              if (e.target.files) onAttach(e.target.files);
              e.target.value = "";
            }}
          />
        </label>
        <AttachmentChips items={attachments} onRemove={onRemoveAttachment} />
      </div>
    </div>
  );
}
