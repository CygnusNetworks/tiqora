import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import type { DynamicFieldDef } from "@/lib/phoneApi";
import type { PhoneTicketFieldsValue } from "./phoneTicketValue";
import { SelectMenu, type SelectMenuItem } from "@/components/ui/SelectMenu";
import { ChevronDownIcon } from "@/components/ui/icons";
import { cn } from "@/lib/cn";
import { DynamicFieldInputs } from "./DynamicFieldInputs";

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
 * "Weitere Felder" of the New-ticket page's phone mode (Decision 7): the
 * rarely used AgentTicketPhone fields — responsible, type, service, SLA and
 * the optional dynamic fields — collapsed by default behind a header that
 * names what is inside. Required dynamic fields, the owner (in
 * `TicketPropsBar`), the pending time, the timer, the time accounting and the
 * attachments live elsewhere on the page. Controlled — the page owns the value.
 */
export function PhoneTicketFields({
  value,
  onChange,
  fields,
}: {
  value: PhoneTicketFieldsValue;
  onChange: (next: PhoneTicketFieldsValue) => void;
  /** Optional dynamic fields only; required ones are rendered by the page. */
  fields: DynamicFieldDef[];
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
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

  const showType = typeItems.length > 1;
  const showService = serviceItems.length > 0;
  const showSla = slaItems.length > 0;
  // What the header promises: the field names, then "n Zusatzfelder".
  const summary = [
    t("ticket.toolbar.responsible"),
    showType && t("ticket.type"),
    showService && t("ticket.service"),
    showSla && t("ticket.sla"),
    fields.length > 0 && t("newTicket.moreFieldsCount", { count: fields.length }),
  ]
    .filter(Boolean)
    .join(", ");

  return (
    <div data-testid="phone-ticket-fields">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={open ? "new-ticket-more-fields" : undefined}
        data-testid="new-ticket-more-toggle"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 rounded py-1 text-left text-[12.5px] text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
      >
        <ChevronDownIcon aria-hidden className={cn("shrink-0 transition-transform duration-150", !open && "-rotate-90")} />
        {t("newTicket.moreFields")}
        <span
          className="rounded-full border border-hairline px-1.5 text-[11px] text-muted"
          data-testid="new-ticket-more-summary"
        >
          {summary}
        </span>
      </button>
      {open && (
        <div id="new-ticket-more-fields" className="mt-2.5 space-y-4" data-testid="new-ticket-more-fields">
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
            {showType && (
              <Picker label={t("ticket.type")} items={typeItems} value={value.typeId} onSelect={(v) => set({ typeId: v })} testId="new-ticket-type" noneLabel={none} />
            )}
            {showService && (
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
            {showSla && (
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
          <DynamicFieldInputs
            fields={fields}
            values={value.dfValues}
            onChange={(name, values) => set({ dfValues: { ...value.dfValues, [name]: values } })}
            testId="new-ticket-df"
          />
        </div>
      )}
    </div>
  );
}
