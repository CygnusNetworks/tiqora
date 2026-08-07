import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Popover } from "@/components/ui/Popover";
import { usePopoverClose } from "@/components/ui/popoverContext";
import { SelectField } from "@/components/ui/SelectField";
import type { SelectMenuItem } from "@/components/ui/SelectMenu";
import { cn } from "@/lib/cn";
import {
  displayToMinutes,
  formatTimeUnits,
  loadTimeUnitMode,
  minutesToDisplay,
  saveTimeUnitMode,
  type TimeUnitMode,
} from "@/lib/timeUnits";
import { TimePresetButtons, TimeUnitToggle } from "./TimeUnitControls";

/**
 * The ticket header's two right-hand counters: who is mentioned and how much
 * time is booked. They carry *state* — a glance answers "am I on this ticket,
 * has anyone booked?" — while the controls live one click deeper in a popover.
 *
 * This replaces the former full-width `MentionsAndTimePanel` below the article
 * list: both are rare actions that were costing a permanently visible box, and
 * the common path (mention while writing, book what the reply took) now runs
 * through the composer instead — see `MentionTextarea` / `ComposerTimeChip`.
 */
export function TicketMetaCounters({ ticketId }: { ticketId: number }) {
  const { t } = useTranslation();

  const mentionsQ = useQuery({
    queryKey: ["tickets", ticketId, "mentions"],
    queryFn: () => api.listTicketMentions(ticketId),
  });
  const timeQ = useQuery({
    queryKey: ["tickets", ticketId, "time-accounting"],
    queryFn: () => api.listTicketTimeAccounting(ticketId),
  });

  const mentions = mentionsQ.data ?? [];
  const entries = timeQ.data ?? [];
  const totalUnits = entries.reduce((sum, r) => sum + Number(r.time_unit), 0);

  return (
    <span className="inline-flex items-center gap-0.5" data-testid="ticket-meta-counters">
      {/* Counter labels interpolate `n`, not `count` — `count` would route the
          lookup through i18next's plural resolver for no benefit here. */}
      <Popover
        align="right"
        label={t("ticket.mentions")}
        panelTestId="ticket-mentions-panel"
        trigger={({ ref, toggleProps }) => (
          <CounterChip
            ref={ref}
            testId="ticket-counter-mentions"
            icon="@"
            value={String(mentions.length)}
            filled={mentions.length > 0}
            label={t("ticket.mentionsCount", { n: mentions.length })}
            {...toggleProps}
          />
        )}
      >
        <MentionsPopover ticketId={ticketId} />
      </Popover>
      <Popover
        align="right"
        label={t("ticket.timeAccounting")}
        panelTestId="ticket-time-panel"
        // Wider than the default w-64: the unit toggle, the amount field and
        // the book button do not fit on one 16rem row without the button
        // wrapping mid-word.
        panelClassName="w-80"
        trigger={({ ref, toggleProps }) => (
          <CounterChip
            ref={ref}
            testId="ticket-counter-time"
            icon="⏱"
            value={t("ticket.timeUnitsShort", { units: formatTimeUnits(totalUnits) })}
            filled={totalUnits > 0}
            label={t("ticket.timeAccountingTotal", { units: formatTimeUnits(totalUnits) })}
            {...toggleProps}
          />
        )}
      >
        <TimePopover ticketId={ticketId} />
      </Popover>
    </span>
  );
}

/* ── chip ─────────────────────────────────────────────────────────────── */

function CounterChip({
  ref,
  icon,
  value,
  filled,
  label,
  testId,
  ...rest
}: {
  ref: React.RefObject<HTMLButtonElement | null>;
  icon: string;
  value: string;
  /** Accent fill once the counter is non-zero — an empty counter stays quiet. */
  filled: boolean;
  label: string;
  testId: string;
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      ref={ref}
      type="button"
      data-testid={testId}
      data-filled={filled ? "true" : undefined}
      aria-label={label}
      title={label}
      className={cn(
        "inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] transition-colors duration-100",
        "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
        filled
          ? "border-accent/40 bg-accent-dim text-accent"
          : "border-transparent text-muted hover:border-hairline hover:bg-surface-subtle hover:text-ink",
      )}
      {...rest}
    >
      <span aria-hidden>{icon}</span>
      <span className="font-mono tabular-nums">{value}</span>
    </button>
  );
}

/* ── mentions ─────────────────────────────────────────────────────────── */

function MentionsPopover({ ticketId }: { ticketId: number }) {
  const { t } = useTranslation();
  const qc = useQueryClient();

  const mentionsQ = useQuery({
    queryKey: ["tickets", ticketId, "mentions"],
    queryFn: () => api.listTicketMentions(ticketId),
  });
  const agentsQ = useQuery({
    queryKey: ["reference", "agents"],
    queryFn: () => api.listReferenceAgents(),
  });

  const invalidate = () =>
    void qc.invalidateQueries({ queryKey: ["tickets", ticketId, "mentions"] });

  const addM = useMutation({
    mutationFn: (userId: number) => api.createTicketMention(ticketId, { user_id: userId }),
    onSuccess: invalidate,
  });
  const removeM = useMutation({
    mutationFn: (id: number) => api.deleteTicketMention(ticketId, id),
    onSuccess: invalidate,
  });

  const mentions = mentionsQ.data ?? [];
  const mentionedIds = new Set(mentions.map((m) => m.user_id));
  const agentItems: SelectMenuItem<number>[] = (agentsQ.data ?? [])
    .filter((a) => !mentionedIds.has(a.id))
    .map((a) => ({ value: a.id, label: a.full_name, hint: a.login }));

  return (
    <div className="space-y-2">
      <p className="text-[10.5px] font-semibold uppercase tracking-[0.1em] text-muted">
        {t("ticket.mentions")}
      </p>
      {mentions.length === 0 ? (
        <p className="text-xs text-muted">{t("ticket.mentionsEmpty")}</p>
      ) : (
        <ul className="space-y-0.5">
          {mentions.map((m) => (
            <li key={m.id} className="flex items-center gap-1.5 text-[13px] text-ink">
              <span className="min-w-0 flex-1 truncate">
                {m.user_name || m.user_login || m.user_id}
              </span>
              <Button
                variant="ghost"
                size="sm"
                className="px-1 py-0 text-muted hover:text-danger"
                aria-label={t("ticket.mentionRemove")}
                data-testid={`ticket-mention-remove-${m.id}`}
                disabled={removeM.isPending}
                onClick={() => removeM.mutate(m.id)}
              >
                ×
              </Button>
            </li>
          ))}
        </ul>
      )}
      <SelectField
        items={agentItems}
        value={null}
        onChange={(id) => addM.mutate(id)}
        placeholder={t("ticket.addMention")}
        testId="ticket-mention-add"
        disabled={addM.isPending || agentItems.length === 0}
      />
    </div>
  );
}

/* ── time accounting ──────────────────────────────────────────────────── */

function TimePopover({ ticketId }: { ticketId: number }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const closePopover = usePopoverClose();
  const [mode, setMode] = useState<TimeUnitMode>(() => loadTimeUnitMode());
  const [text, setText] = useState("");

  useEffect(() => saveTimeUnitMode(mode), [mode]);

  const timeQ = useQuery({
    queryKey: ["tickets", ticketId, "time-accounting"],
    queryFn: () => api.listTicketTimeAccounting(ticketId),
  });

  const invalidate = () =>
    void qc.invalidateQueries({ queryKey: ["tickets", ticketId, "time-accounting"] });

  const bookM = useMutation({
    mutationFn: (value: number) => api.createTicketTimeAccounting(ticketId, { time_unit: value }),
    onSuccess: () => {
      setText("");
      invalidate();
      closePopover();
    },
  });
  const removeM = useMutation({
    mutationFn: (id: number) => api.deleteTicketTimeAccounting(ticketId, id),
    onSuccess: invalidate,
  });

  const entries = timeQ.data ?? [];
  const total = entries.reduce((sum, r) => sum + Number(r.time_unit), 0);
  const parsed = displayToMinutes(text, mode);
  const canBook = parsed !== null;

  const handleModeChange = (nextMode: TimeUnitMode) => {
    setMode(nextMode);
    setText(parsed === null ? "" : minutesToDisplay(parsed, nextMode));
  };

  const handleBook = () => {
    if (parsed !== null) bookM.mutate(parsed);
  };

  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between gap-2">
        <p className="text-[10.5px] font-semibold uppercase tracking-[0.1em] text-muted">
          {t("ticket.timeAccounting")}
        </p>
        <span className="font-mono text-[11px] tabular-nums text-muted">
          {t("ticket.timeUnitsShort", { units: formatTimeUnits(total) })}
        </span>
      </div>
      {entries.length > 0 && (
        <ul className="space-y-0.5">
          {entries.map((r) => (
            <li key={r.id} className="flex items-center gap-1.5 text-[13px] text-ink">
              <span className="w-12 shrink-0 font-mono tabular-nums">
                {formatTimeUnits(Number(r.time_unit))}
              </span>
              <span className="min-w-0 flex-1 truncate text-muted">
                {r.create_by_login || r.create_by}
              </span>
              <Button
                variant="ghost"
                size="sm"
                className="px-1 py-0 text-muted hover:text-danger"
                aria-label={t("ticket.timeRemove")}
                data-testid={`ticket-time-remove-${r.id}`}
                disabled={removeM.isPending}
                onClick={() => removeM.mutate(r.id)}
              >
                ×
              </Button>
            </li>
          ))}
        </ul>
      )}
      {/* Amount first, then the shortcuts that fill it, then the commit —
          the button on its own row so its label can never wrap. */}
      <div className="flex items-center gap-1.5">
        <TimeUnitToggle
          mode={mode}
          onChange={handleModeChange}
          size="sm"
          testId="ticket-time-mode"
        />
        <div className="flex min-w-0 flex-1 items-center gap-1 rounded border border-hairline bg-surface px-2 py-1">
          <input
            type="number"
            min="0"
            step={mode === "min" ? 1 : 0.25}
            inputMode="decimal"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && canBook) {
                e.preventDefault();
                handleBook();
              }
            }}
            placeholder="0"
            aria-label={t("ticket.timeUnits")}
            data-testid="ticket-time-units"
            className="w-full min-w-0 border-none bg-transparent text-right font-mono text-xs tabular-nums text-ink focus:outline-none"
          />
          <span className="shrink-0 text-[10px] text-muted">
            {mode === "min" ? t("ticket.timeUnitAbbrev") : t("ticket.timeUnitHoursAbbrev")}
          </span>
        </div>
      </div>
      <TimePresetButtons
        testId="ticket-time-preset"
        onPick={(minutes) => setText(minutesToDisplay(minutes, mode))}
      />
      <Button
        variant="primary"
        size="sm"
        className="w-full"
        data-testid="ticket-time-book"
        disabled={!canBook || bookM.isPending}
        onClick={handleBook}
      >
        {t("ticket.addTime")}
      </Button>
      <p className="text-[11px] text-muted">{t("ticket.timeComposerHint")}</p>
    </div>
  );
}
