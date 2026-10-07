import { useEffect, useRef, type CSSProperties, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { TicketListItem } from "@/lib/api";
import {
  BULK_FIELDS,
  ROOT_USER_ID,
  clearDraftField,
  draftFields,
  draftReady,
  isClosedStateId,
  isPendingStateId,
  pendingQuickPicks,
  stateType,
  type BulkDraft,
  type BulkField,
  type BulkRefs,
} from "@/lib/bulkDraft";
import { Button } from "@/components/ui/Button";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { SelectField } from "@/components/ui/SelectField";
import type { SelectMenuItem } from "@/components/ui/SelectMenu";
import { Spinner } from "@/components/ui/Spinner";
import { LockIcon } from "@/components/ui/icons";
import { cn } from "@/lib/cn";
import { formatDateTime } from "@/lib/format";
import { priorityColorVar, priorityIdFromName, priorityLabel } from "@/lib/priority";
import { stateColorVar, stateLabel } from "@/lib/status";
import { fromZonedInputValue } from "@/lib/timeZone";

type Chip = { key: string; label: string; color?: string; icon?: ReactNode; count: number };

/**
 * Side panel of the queue's bulk action: every field with what the selected
 * tickets have now and what they get. The quick actions in the selection bar
 * prefill it; nothing is sent until "apply". One PATCH per ticket carries all
 * picked fields.
 */
export function BulkEditPanel({
  count,
  known,
  refs,
  draft,
  onChange,
  meId,
  applying,
  onApply,
  onDiscard,
  onClose,
  openField,
  className,
}: {
  /** Number of selected tickets (may exceed `known` with "select all matches"). */
  count: number;
  /** The selected tickets loaded on this page — the "now" column. */
  known: TicketListItem[];
  refs: BulkRefs;
  draft: BulkDraft;
  onChange: (draft: BulkDraft) => void;
  meId?: number;
  applying: boolean;
  onApply: () => void;
  onDiscard: () => void;
  onClose: () => void;
  /** Opens that field's menu once (e.g. "Verschieben…" → the queue list); bump `seq` to reopen. */
  openField?: { field: BulkField; seq: number } | null;
  className?: string;
}) {
  const { t, i18n } = useTranslation();
  const rootRef = useRef<HTMLElement>(null);
  const fields = draftFields(draft);
  const pending = isPendingStateId(refs.states, draft.state_id);

  useEffect(() => {
    if (!openField) return;
    const trigger = rootRef.current?.querySelector<HTMLElement>(
      `[data-testid="queue-bulk-${openField.field}-select"]`,
    );
    trigger?.click();
  }, [openField]);

  const fieldLabel = (f: BulkField) => t(`queue.bulk.field${f.charAt(0).toUpperCase()}${f.slice(1)}`);
  const set = (patch: BulkDraft) => onChange({ ...draft, ...patch });

  const chipsFor = (f: BulkField): Chip[] => {
    const byKey = new Map<string, Chip>();
    for (const ticket of known) {
      let chip: Omit<Chip, "count">;
      if (f === "state") {
        chip = { key: ticket.state ?? "", label: stateLabel(t, ticket.state), color: stateColorVar(ticket.state) };
      } else if (f === "priority") {
        chip = {
          key: String(ticket.priority_id),
          label: priorityLabel(t, ticket.priority),
          color: priorityColorVar(priorityIdFromName(ticket.priority) ?? ticket.priority_id),
        };
      } else if (f === "owner") {
        chip = { key: String(ticket.owner_id), label: ticket.owner_name ?? ticket.owner_login ?? `#${ticket.owner_id}` };
      } else if (f === "queue") {
        chip = { key: String(ticket.queue_id), label: ticket.queue_name ?? `#${ticket.queue_id}` };
      } else {
        const locked = ticket.lock === "lock";
        chip = {
          key: locked ? "lock" : "unlock",
          label: t(locked ? "queue.bulk.lockedNow" : "queue.bulk.unlockedNow"),
          icon: locked ? <LockIcon className="h-3 w-3" /> : undefined,
        };
      }
      const prev = byKey.get(chip.key);
      byKey.set(chip.key, { ...chip, count: (prev?.count ?? 0) + 1 });
    }
    return [...byKey.values()].sort((a, b) => b.count - a.count);
  };

  const stateItems: SelectMenuItem<number>[] = refs.states.map((s) => ({
    value: s.id,
    label: stateLabel(t, s.name, s.name),
  }));
  const agentItems = meId == null
    ? refs.agents
    : [...refs.agents.filter((a) => a.value === meId), ...refs.agents.filter((a) => a.value !== meId)];
  const priorities = [...refs.priorities].sort(
    (a, b) => (priorityIdFromName(a.name) ?? a.id) - (priorityIdFromName(b.name) ?? b.id),
  );
  const pickedPriority = refs.priorities.find((p) => p.id === draft.priority_id);

  const controls: Record<BulkField, ReactNode> = {
    state: (
      <div className="space-y-2.5">
        <SelectField
          items={stateItems}
          value={draft.state_id ?? null}
          onChange={(id) =>
            set({
              state_id: id,
              pending_until: isPendingStateId(refs.states, id)
                ? (draft.pending_until ?? pendingQuickPicks()[0].value)
                : undefined,
            })
          }
          placeholder={t("queue.bulk.unchanged")}
          aria-label={fieldLabel("state")}
          testId="queue-bulk-state-select"
        />
        {pending && (
          <div className="space-y-1.5" data-testid="queue-bulk-pending">
            <span className="block text-[12px] text-muted">{t("queue.bulk.pendingUntil")}</span>
            <div className="flex flex-wrap items-center gap-1.5">
              {pendingQuickPicks().map((pick) => (
                <button
                  key={pick.key}
                  type="button"
                  aria-pressed={draft.pending_until === pick.value}
                  onClick={() => set({ pending_until: pick.value })}
                  className={cn(
                    "rounded-md border px-2 py-1 text-[12px] transition-colors duration-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
                    draft.pending_until === pick.value
                      ? "border-accent bg-accent-dim font-medium text-accent"
                      : "border-hairline bg-surface text-ink hover:bg-surface-subtle",
                  )}
                >
                  {t(`queue.bulk.${pick.key}`)}
                </button>
              ))}
              <input
                type="datetime-local"
                value={draft.pending_until ?? ""}
                onChange={(e) => set({ pending_until: e.target.value || undefined })}
                aria-label={t("queue.bulk.pendingUntil")}
                data-testid="queue-bulk-pending-time"
                className="rounded-md border border-hairline bg-surface px-2 py-1 text-[12px] text-ink focus:outline-none focus:ring-1 focus:ring-accent"
              />
            </div>
            {!fromZonedInputValue(draft.pending_until) && (
              <p className="text-[12px] text-danger">{t("queue.bulk.pendingTimeMissing")}</p>
            )}
          </div>
        )}
      </div>
    ),
    priority: (
      <div className="flex flex-wrap items-center gap-2">
        <div role="radiogroup" aria-label={fieldLabel("priority")} className="flex flex-wrap gap-1">
          {priorities.map((p) => {
            const rank = priorityIdFromName(p.name) ?? p.id;
            const on = draft.priority_id === p.id;
            const label = priorityLabel(t, p.name, p.name);
            return (
              <button
                key={p.id}
                type="button"
                role="radio"
                aria-checked={on}
                aria-label={label}
                title={label}
                data-testid={`queue-bulk-priority-${p.id}`}
                onClick={() => set({ priority_id: p.id })}
                style={{ "--prio": priorityColorVar(rank) } as CSSProperties}
                className={cn(
                  "flex h-8 items-end gap-[2px] rounded-md border px-2 pb-2 transition-colors duration-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
                  on
                    ? "border-[color:var(--prio)] bg-[color-mix(in_srgb,var(--prio)_12%,transparent)]"
                    : "border-transparent hover:bg-surface-subtle",
                )}
              >
                {[1, 2, 3, 4, 5].map((bar) => (
                  <span
                    key={bar}
                    aria-hidden
                    className="w-[3px] rounded-[1px]"
                    style={{
                      height: 4 + bar * 2,
                      background: bar <= rank ? "var(--prio)" : "var(--color-hairline)",
                    }}
                  />
                ))}
              </button>
            );
          })}
        </div>
        <span className={cn("text-[12px]", pickedPriority ? "font-medium text-ink" : "text-muted")}>
          {pickedPriority ? priorityLabel(t, pickedPriority.name, pickedPriority.name) : t("queue.bulk.unchanged")}
        </span>
      </div>
    ),
    owner: (
      <SelectField
        items={agentItems}
        value={draft.owner_id ?? null}
        onChange={(id) => set({ owner_id: id })}
        placeholder={t("queue.bulk.unchanged")}
        aria-label={fieldLabel("owner")}
        testId="queue-bulk-owner-select"
      />
    ),
    queue: (
      <SelectField
        items={refs.queues}
        value={draft.queue_id ?? null}
        onChange={(id) => set({ queue_id: id })}
        placeholder={t("queue.bulk.unchanged")}
        aria-label={fieldLabel("queue")}
        testId="queue-bulk-queue-select"
      />
    ),
    lock: (
      <SegmentedControl
        items={[
          { value: "lock", label: t("queue.bulk.lockAction") },
          { value: "unlock", label: t("queue.bulk.unlockAction") },
        ]}
        value={draft.lock ?? null}
        onChange={(lock) => set({ lock })}
        aria-label={fieldLabel("lock")}
        testId="queue-bulk-lock"
      />
    ),
  };

  // What applying will do, beyond the field list.
  const notes: string[] = [];
  if (isClosedStateId(refs.states, draft.state_id)) notes.push(t("queue.bulk.conseqClosed"));
  if (pending) {
    const when = fromZonedInputValue(draft.pending_until);
    if (when) {
      const reminder = stateType(refs.states, draft.state_id) === "pending reminder";
      notes.push(
        t(reminder ? "queue.bulk.conseqReminder" : "queue.bulk.conseqAutoClose", {
          when: formatDateTime(when, i18n.language),
        }),
      );
    }
  }
  if (draft.owner_id != null) {
    const others = known.filter((tk) => tk.owner_id !== draft.owner_id && tk.owner_id !== ROOT_USER_ID);
    if (others.length) {
      const names = [...new Set(others.map((tk) => tk.owner_name ?? tk.owner_login ?? `#${tk.owner_id}`))];
      notes.push(
        t("queue.bulk.ownerOverwrite", {
          count: others.length,
          names: new Intl.ListFormat(i18n.language, { type: "conjunction" }).format(names),
        }),
      );
    }
  }

  const summary = fields.length
    ? t("queue.bulk.summary", {
        count,
        fields: new Intl.ListFormat(i18n.language, { type: "conjunction" }).format(fields.map(fieldLabel)),
      })
    : t("queue.bulk.summaryNone");
  const applyLabel =
    fields.length > 1
      ? t("queue.bulk.applyMany", { count: fields.length })
      : fields[0] === "state" && isClosedStateId(refs.states, draft.state_id)
        ? t("queue.bulk.applyClose", { count })
        : t("queue.bulk.apply", { count });
  const partial = known.length < count;

  return (
    <aside
      ref={rootRef}
      aria-label={t("queue.bulk.panelTitle", { count })}
      data-testid="queue-bulk-panel"
      className={cn("flex flex-col border-l border-hairline bg-surface", className)}
    >
      <div className="flex items-center gap-2 border-b border-hairline py-2.5 pl-4 pr-2">
        <h2 className="mr-auto text-[13px] font-semibold text-ink">{t("queue.bulk.panelTitle", { count })}</h2>
        <Button
          variant="ghost"
          size="sm"
          aria-label={t("queue.bulk.closePanel")}
          data-testid="queue-bulk-panel-close"
          onClick={onClose}
        >
          ✕
        </Button>
      </div>

      <div className="flex-1">
        {BULK_FIELDS.map((f) => {
          const changed = fields.includes(f);
          const chips = chipsFor(f);
          return (
            <section
              key={f}
              data-testid={`queue-bulk-field-${f}`}
              className={cn("space-y-2 border-b border-hairline px-4 py-3", changed && "bg-accent-dim")}
            >
              <div className="flex min-h-[22px] items-center gap-2">
                <span className={cn("mr-auto text-[13px] font-medium", changed ? "text-accent" : "text-ink")}>
                  {fieldLabel(f)}
                </span>
                {changed && (
                  <button
                    type="button"
                    data-testid={`queue-bulk-field-${f}-clear`}
                    onClick={() => onChange(clearDraftField(draft, f))}
                    className="text-[12px] text-muted hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
                  >
                    {t("queue.bulk.keepUnchanged")}
                  </button>
                )}
              </div>
              {chips.length > 0 && (
                <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[12px]">
                  <span className="text-muted">{t(partial ? "queue.bulk.nowOnPage" : "queue.bulk.now")}</span>
                  {chips.map((chip) => (
                    <span
                      key={chip.key}
                      className={cn(
                        "inline-flex items-center gap-1 text-ink",
                        changed && "line-through decoration-muted opacity-60",
                      )}
                    >
                      {chip.color && (
                        <span aria-hidden className="h-2 w-2 rounded-full" style={{ background: chip.color }} />
                      )}
                      {chip.icon}
                      {chip.label}
                      <span className="font-mono text-[11px] tabular-nums text-muted">{chip.count}</span>
                    </span>
                  ))}
                </div>
              )}
              {controls[f]}
            </section>
          );
        })}
      </div>

      <div className="sticky bottom-0 space-y-2.5 border-t border-hairline bg-surface-subtle px-4 py-3">
        <p className="text-[12.5px] text-muted" data-testid="queue-bulk-summary">
          <span className="text-ink">{summary}</span>
          {notes.map((note) => (
            <span key={note}> {note}</span>
          ))}
        </p>
        <div className="flex justify-end gap-2">
          <Button
            variant="ghost"
            size="sm"
            disabled={!fields.length || applying}
            data-testid="queue-bulk-discard"
            onClick={onDiscard}
          >
            {t("queue.bulk.discard")}
          </Button>
          <Button
            variant="primary"
            size="sm"
            disabled={!draftReady(draft, refs.states) || applying}
            data-testid="queue-bulk-apply"
            onClick={onApply}
          >
            {applying && <Spinner className="h-3 w-3" />}
            {applyLabel}
          </Button>
        </div>
      </div>
    </aside>
  );
}
