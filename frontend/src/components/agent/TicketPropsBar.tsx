import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { SelectMenu, type SelectMenuItem } from "@/components/ui/SelectMenu";
import { ChevronDownIcon } from "@/components/ui/icons";
import { Avatar } from "@/components/ui/Avatar";
import { priorityColorVar, priorityIdFromName, priorityName } from "@/lib/priority";
import { stateColorVar, stateLabel } from "@/lib/status";
import { cn } from "@/lib/cn";

type Ref = { id: number; name: string };
type StateRef = Ref & { type_name: string };
export type PropsBarAgent = { id: number; full_name: string; login: string };

export type TicketPropsBarProps = {
  queues: Ref[];
  queueId: number | null;
  onQueueChange: (id: number) => void;
  /** Why this queue was chosen ("wie letztes Ticket von …"); none when picked by hand. */
  queueSource?: string | null;
  /** Phone variant only: the owner cell. Absent = the e-mail bar (three cells). */
  owner?: {
    agents: PropsBarAgent[];
    value: number | null;
    onChange: (id: number) => void;
    source?: string | null;
  };
  priorities: Ref[];
  priorityId: number | null;
  onPriorityChange: (id: number) => void;
  states: StateRef[];
  stateId: number | null;
  onStateChange: (id: number) => void;
};

function Dot({ color }: { color: string }) {
  return (
    <span
      aria-hidden
      className="h-2 w-2 shrink-0 rounded-full"
      style={{ background: color }}
    />
  );
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  const first = parts[0]?.[0] ?? "";
  const last = parts.length > 1 ? parts[parts.length - 1][0] : "";
  return (first + last).toUpperCase() || "?";
}

/** Queue path with the parent segments muted: "Parent › Leaf". */
function QueueName({ name }: { name: string }) {
  const parts = name.split("::");
  const leaf = parts.pop() ?? name;
  return (
    <span className="min-w-0 truncate">
      {parts.length > 0 && <span className="text-muted">{parts.join(" › ")} › </span>}
      {leaf}
    </span>
  );
}

function PropCell({
  label,
  items,
  value,
  onSelect,
  display,
  lead,
  source,
  testId,
  placeholder,
  className,
}: {
  label: string;
  items: SelectMenuItem<number>[];
  value: number | null;
  onSelect: (v: number) => void;
  display: ReactNode;
  lead?: ReactNode;
  source?: string | null;
  testId: string;
  placeholder: string;
  className?: string;
}) {
  return (
    <SelectMenu
      items={items}
      value={value ?? undefined}
      onSelect={onSelect}
      placeholder={placeholder}
      panelTestId={`${testId}-panel`}
      trigger={({ open, ref, toggleProps }) => (
        <button
          ref={ref}
          type="button"
          data-testid={testId}
          {...toggleProps}
          className={cn(
            "grid min-w-0 content-start gap-px bg-surface-subtle px-3 pb-[9px] pt-2 text-left transition-colors duration-100 hover:bg-accent-dim focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent",
            open && "bg-accent-dim",
            className,
          )}
        >
          <span className="text-[11px] font-semibold uppercase tracking-[0.06em] text-muted">
            {label}
          </span>
          <span className="flex min-w-0 items-center gap-[7px] text-[13.5px] font-medium text-ink">
            {lead}
            <span className="min-w-0 truncate" data-testid={`${testId}-value`}>
              {display}
            </span>
            <ChevronDownIcon
              className={cn(
                "ml-auto shrink-0 text-muted transition-transform duration-150",
                open && "rotate-180",
              )}
            />
          </span>
          {source && (
            <span
              className="truncate text-[11.5px] text-green"
              data-testid={`${testId}-source`}
            >
              {source}
            </span>
          )}
        </button>
      )}
    />
  );
}

/**
 * The New-ticket property bar: one bordered row of cells (Queue · Besitzer ·
 * Priorität · Status for a phone ticket, Queue · Priorität · Status for an
 * e-mail). Each cell is a `SelectMenu` trigger — label, value with chevron and
 * an optional green line saying where the value came from. Two columns below
 * 700 px.
 */
export function TicketPropsBar({
  queues,
  queueId,
  onQueueChange,
  queueSource,
  owner,
  priorities,
  priorityId,
  onPriorityChange,
  states,
  stateId,
  onStateChange,
}: TicketPropsBarProps) {
  const { t } = useTranslation();
  const none = t("admin.form.selectPlaceholder");

  const queueItems = queues.map((q) => ({ value: q.id, label: q.name }));
  const priorityItems = priorities.map((p) => ({
    value: p.id,
    label: priorityName(p.name) ?? p.name,
  }));
  const stateItems = states.map((s) => ({ value: s.id, label: stateLabel(t, s.name) }));
  const agentItems = (owner?.agents ?? []).map((a) => ({
    value: a.id,
    label: a.full_name || a.login,
    hint: a.login,
  }));

  const queue = queues.find((q) => q.id === queueId);
  const priority = priorities.find((p) => p.id === priorityId);
  const state = states.find((s) => s.id === stateId);
  const agent = owner?.agents.find((a) => a.id === owner.value);
  const agentName = agent ? agent.full_name || agent.login : null;

  return (
    <div
      role="group"
      aria-label={t("newTicket.propsLabel")}
      data-testid="ticket-props-bar"
      className={cn(
        "grid grid-cols-2 gap-px overflow-hidden rounded-[9px] border border-hairline bg-hairline",
        owner
          ? "min-[700px]:grid-cols-[1.6fr_1.1fr_0.9fr_0.9fr]"
          : "min-[700px]:grid-cols-[1.6fr_0.9fr_0.9fr]",
      )}
    >
      <PropCell
        label={t("newTicket.queue")}
        items={queueItems}
        value={queueId}
        onSelect={onQueueChange}
        display={queue ? <QueueName name={queue.name} /> : t("newTicket.noQueues")}
        source={queueSource}
        testId="new-ticket-queue"
        placeholder={t("newTicket.noQueues")}
      />
      {owner && (
        <PropCell
          label={t("ticket.owner")}
          items={agentItems}
          value={owner.value}
          onSelect={owner.onChange}
          display={agentName ?? none}
          lead={
            agentName ? <Avatar initials={initials(agentName)} size={20} /> : undefined
          }
          source={owner.source}
          testId="new-ticket-owner"
          placeholder={none}
        />
      )}
      <PropCell
        label={t("newTicket.priority")}
        items={priorityItems}
        value={priorityId}
        onSelect={onPriorityChange}
        display={priority ? (priorityName(priority.name) ?? priority.name) : none}
        lead={
          priority ? (
            <Dot color={priorityColorVar(priorityIdFromName(priority.name))} />
          ) : undefined
        }
        testId="new-ticket-priority"
        placeholder={none}
      />
      <PropCell
        label={t("newTicket.state")}
        items={stateItems}
        value={stateId}
        onSelect={onStateChange}
        display={state ? stateLabel(t, state.name) : none}
        lead={state ? <Dot color={stateColorVar(state.type_name || state.name)} /> : undefined}
        testId="new-ticket-state"
        placeholder={none}
        // Three cells in two columns: the last one spans the row.
        className={owner ? undefined : "col-span-2 min-[700px]:col-span-1"}
      />
    </div>
  );
}
