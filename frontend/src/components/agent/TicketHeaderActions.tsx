import { useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import type { TicketDetail } from "@/lib/api";
import { useAuth } from "@/auth/AuthContext";
import { Avatar } from "@/components/ui/Avatar";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Menu, MenuItem, MenuLabel, MenuSeparator } from "@/components/ui/Menu";
import { SelectMenu, type SelectMenuItem } from "@/components/ui/SelectMenu";
import { PriorityChip, StateChip } from "@/components/ui/StatusChip";
import { formatDateTime } from "@/lib/format";
import { escalationLevel, stateLabel } from "@/lib/status";
import { cn } from "@/lib/cn";
import { ReplyDialog } from "./ReplyDialog";
import { articleSortKey } from "@/lib/article";
import { flattenQueues } from "./QueueTree";
import { CustomerPickerDialog, LinkDialog, MergeDialog, PendingDialog } from "./ActionToolbar";
import { ticketPerms, usePatchTicket } from "@/lib/ticket";

/**
 * Ticket-zoom header, "Variante 2 — Zwei Ebenen": state next to the title,
 * people below. Three rows:
 *
 *   1. identity — ticket number, queue breadcrumb, lock badge
 *   2. title + state/priority chips + humanized SLA chip, actions right
 *   3. people — owner/responsible/customer as avatar pills, relative
 *      changed-timestamp right (absolute created/changed in the tooltip)
 *
 * All values stay clickable dropdowns/dialogs; ActionToolbar keeps owning
 * the dialog implementations — this component only re-wires their triggers.
 */
export function TicketHeaderActions({
  ticket,
  canNote,
  onOpenNote,
  overflowMenu,
}: {
  ticket: TicketDetail;
  /** Whether the agent may reply / add notes (``note`` permission). */
  canNote: boolean;
  /** Opens the internal-note composer at the bottom of the article list. */
  onOpenNote: () => void;
  /** Optional ⋮ overflow menu rendered at the end of the actions row. */
  overflowMenu?: ReactNode;
}) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";
  const { user } = useAuth();
  const navigate = useNavigate();
  const ticketId = ticket.id;
  const perms = ticketPerms(ticket);
  const noPerm = t("ticket.toolbar.noPermission");
  const patch = usePatchTicket(ticketId);

  const prioritiesQ = useQuery({
    queryKey: ["reference", "priorities"],
    queryFn: () => api.listReferencePriorities(),
  });
  const statesQ = useQuery({
    queryKey: ["reference", "states"],
    queryFn: () => api.listReferenceStates(),
  });
  const queuesQ = useQuery({ queryKey: ["queues"], queryFn: () => api.listQueues() });
  const agentsQ = useQuery({
    queryKey: ["reference", "agents"],
    queryFn: () => api.listReferenceAgents(),
  });
  // Latest article drives the header's "Antworten" shortcut — same target a
  // customer-visible per-article reply would pick, so it stays cheap to
  // find rather than adding a dedicated endpoint.
  const articlesQ = useQuery({
    queryKey: ["tickets", ticketId, "articles"],
    queryFn: () => api.listArticles(ticketId),
  });

  const states = statesQ.data ?? [];
  const closedStates = states.filter((s) => s.type_name.startsWith("closed"));
  const pendingStates = states.filter((s) => s.type_name.startsWith("pending"));
  const primaryStates = states.filter(
    (s) => !s.type_name.startsWith("closed") && !s.type_name.startsWith("pending"),
  );

  const articles = articlesQ.data ?? [];
  const visibleArticles = articles.filter((a) => a.is_visible_for_customer);
  const replyTarget = [...(visibleArticles.length > 0 ? visibleArticles : articles)].sort(
    (a, b) => articleSortKey(b) - articleSortKey(a),
  )[0];

  const isLocked = Boolean(ticket.lock && ticket.lock.toLowerCase() !== "unlock");

  const queueItems: SelectMenuItem<number>[] = flattenQueues(queuesQ.data ?? [])
    .filter((q) => q.valid)
    .map((q) => ({ value: q.id, label: q.name }));
  const agents = agentsQ.data ?? [];
  const agentItems: SelectMenuItem<number>[] = agents.map((a) => ({
    value: a.id,
    label: a.full_name,
    hint: a.login,
  }));
  const responsibleAgent = agents.find((a) => a.id === ticket.responsible_user_id);
  const ownerName = ticket.owner_name || ticket.owner_login || "—";
  const customerLabel = ticket.customer_user_id || ticket.customer_id || "—";

  // Which modal dialog is open (null = none) — mirrors ActionToolbar's own
  // single-dialog-at-a-time state, kept separately since this is a second,
  // independent trigger surface for the same dialogs.
  const [dialog, setDialog] = useState<"customer" | "pending" | "link" | "merge" | null>(null);
  const [replyOpen, setReplyOpen] = useState(false);

  const toggleWatch = () => {
    if (!user) return;
    patch.mutate(
      ticket.is_watched ? { unwatch_user_id: user.id } : { watcher_user_id: user.id },
    );
  };

  return (
    <div className="space-y-2 print:hidden" data-testid="ticket-header-actions">
      {/* ── Row 1: identity ─────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
        <span className="rounded bg-accent-dim px-1.5 py-0.5 font-mono text-[12px] text-accent">
          {ticket.tn}
        </span>
        <QueueBreadcrumb
          items={queueItems}
          value={ticket.queue_id}
          valueLabel={ticket.queue_name}
          disabledTitle={!perms.move_into ? noPerm : undefined}
          rootLabel={t("ticket.queuesRoot")}
          placeholder={t("ticket.dialog.selectPlaceholder")}
          onSelect={(id) => patch.mutate({ queue_id: id })}
        />
        {isLocked && <Badge tone="warn">{ticket.lock}</Badge>}
      </div>

      {/* ── Row 2: title + state + actions ──────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="min-w-0 max-w-full font-display text-xl font-semibold leading-tight text-ink">
          {ticket.title || t("ticket.noTitle")}
        </h1>
        <span title={!perms.rw ? noPerm : undefined} className={cn(!perms.rw && "opacity-60")}>
          <Menu
            align="left"
            panelTestId="ticket-pill-state-menu"
            trigger={({ ref, toggleProps }) => (
              <button
                ref={ref}
                type="button"
                data-testid="ticket-pill-state"
                disabled={!perms.rw}
                {...toggleProps}
                className="block"
              >
                <StateChip state={ticket.state} />
              </button>
            )}
          >
            <MenuLabel>{t("ticket.state")}</MenuLabel>
            {primaryStates.map((s) => (
              <MenuItem
                key={s.id}
                selected={s.id === ticket.state_id}
                onSelect={() => patch.mutate({ state_id: s.id })}
              >
                {stateLabel(t, s.name)}
              </MenuItem>
            ))}
            <MenuSeparator />
            <MenuItem testId="pill-state-pending" onSelect={() => setDialog("pending")}>
              {t("ticket.toolbar.pending")}…
            </MenuItem>
            {closedStates.length > 0 && (
              <>
                <MenuSeparator />
                <MenuLabel>{t("ticket.toolbar.close")}</MenuLabel>
                {closedStates.map((s) => (
                  <MenuItem
                    key={s.id}
                    selected={s.id === ticket.state_id}
                    onSelect={() => patch.mutate({ state_id: s.id })}
                  >
                    {stateLabel(t, s.name)}
                  </MenuItem>
                ))}
              </>
            )}
          </Menu>
        </span>
        <span
          title={!perms.priority ? noPerm : undefined}
          className={cn(!perms.priority && "opacity-60")}
        >
          <Menu
            align="left"
            panelTestId="ticket-pill-priority-menu"
            trigger={({ ref, toggleProps }) => (
              <button
                ref={ref}
                type="button"
                data-testid="ticket-pill-priority"
                disabled={!perms.priority}
                {...toggleProps}
                className="block"
              >
                <PriorityChip priority={ticket.priority} priorityId={ticket.priority_id} />
              </button>
            )}
          >
            <MenuLabel>{t("ticket.priority")}</MenuLabel>
            {(prioritiesQ.data ?? []).map((p) => (
              <MenuItem
                key={p.id}
                selected={p.id === ticket.priority_id}
                onSelect={() => patch.mutate({ priority_id: p.id })}
              >
                {p.name}
              </MenuItem>
            ))}
          </Menu>
        </span>
        <SlaChip ticket={ticket} />

        <span className="ml-auto flex items-center gap-2">
          <span title={!canNote ? noPerm : undefined} className="inline-flex">
            <Button
              variant="primary"
              size="sm"
              disabled={!canNote || !replyTarget}
              data-testid="ticket-actions-reply"
              onClick={() => setReplyOpen(true)}
            >
              ↩ {t("ticket.reply")}
            </Button>
          </span>
          <span title={!canNote ? noPerm : undefined} className="inline-flex">
            <Button
              variant="secondary"
              size="sm"
              disabled={!canNote}
              data-testid="ticket-actions-note"
              onClick={onOpenNote}
            >
              ＋ {t("ticket.addNote")}
            </Button>
          </span>
          <Menu
            align="right"
            panelTestId="ticket-actions-more-menu"
            trigger={({ ref, toggleProps }) => (
              <button
                ref={ref}
                type="button"
                data-testid="ticket-actions-more"
                className="inline-flex items-center gap-1.5 rounded-md border border-hairline bg-surface px-2 py-1 text-xs font-medium text-ink transition-colors duration-100 hover:bg-surface-subtle"
                {...toggleProps}
              >
                {t("ticket.moreActions")} ⌄
              </button>
            )}
          >
            <MenuLabel>{t("ticket.actionsGroupAssign")}</MenuLabel>
            {perms.rw && (
              <MenuItem
                testId="more-lock"
                onSelect={() => patch.mutate({ lock: isLocked ? "unlock" : "lock" })}
              >
                {isLocked ? t("ticket.toolbar.unlock") : t("ticket.toolbar.lock")}
              </MenuItem>
            )}
            {user && (
              <MenuItem testId="more-watch" selected={ticket.is_watched} onSelect={toggleWatch}>
                {ticket.is_watched ? t("ticket.toolbar.unwatch") : t("ticket.toolbar.watch")}
              </MenuItem>
            )}

            <MenuSeparator />
            <MenuLabel>{t("ticket.actionsGroupOrganize")}</MenuLabel>
            {perms.rw && (
              <MenuItem testId="more-link" onSelect={() => setDialog("link")}>
                {t("ticket.toolbar.link")}
              </MenuItem>
            )}
            {perms.rw && (
              <MenuItem testId="more-merge" onSelect={() => setDialog("merge")}>
                {t("ticket.toolbar.merge")}
              </MenuItem>
            )}

            <MenuSeparator />
            <MenuLabel>{t("ticket.actionsGroupMisc")}</MenuLabel>
            <MenuItem testId="more-print" onSelect={() => window.print()}>
              {t("ticket.toolbar.print")}
            </MenuItem>
            <MenuItem
              testId="more-appointment"
              onSelect={() => void navigate({ to: "/agent/calendar" })}
            >
              {t("ticket.toolbar.appointment")}
            </MenuItem>
          </Menu>
          {overflowMenu && <span data-testid="ticket-header-overflow">{overflowMenu}</span>}
        </span>
      </div>

      {/* ── Row 3: people + timestamps ──────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-t border-hairline pt-2">
        <PersonSelect
          label={t("ticket.owner")}
          name={ownerName}
          testId="ticket-pill-owner"
          panelTestId="ticket-pill-owner-menu"
          disabledTitle={!perms.owner ? noPerm : undefined}
          items={agentItems}
          value={ticket.owner_id}
          placeholder={t("ticket.dialog.selectPlaceholder")}
          onSelect={(id) => patch.mutate({ owner_id: id })}
        />
        <PersonSelect
          label={t("ticket.toolbar.responsible")}
          name={responsibleAgent?.full_name || "—"}
          testId="ticket-pill-responsible"
          panelTestId="ticket-pill-responsible-menu"
          disabledTitle={!perms.owner ? noPerm : undefined}
          muted={!ticket.responsible_user_id}
          items={agentItems}
          value={ticket.responsible_user_id ?? null}
          placeholder={t("ticket.dialog.selectPlaceholder")}
          onSelect={(id) => patch.mutate({ responsible_id: id })}
        />
        <PersonShell
          label={t("ticket.customer")}
          name={customerLabel}
          email={ticket.customer_user_id}
          avatarTone="customer"
          testId="ticket-pill-customer"
          disabledTitle={!perms.rw ? noPerm : undefined}
          onClick={perms.rw ? () => setDialog("customer") : undefined}
        />
        <span
          className="ml-auto inline-flex items-center gap-1 font-mono text-[11px] tabular-nums text-muted"
          data-testid="ticket-header-timestamps"
          title={`${t("ticket.created")}: ${formatDateTime(ticket.create_time, locale)} · ${t("ticket.changed")}: ${formatDateTime(ticket.change_time, locale)}`}
        >
          <span aria-hidden>⏱</span>
          {formatDateTime(ticket.change_time, locale)}
        </span>
      </div>

      {replyTarget && (
        <ReplyDialog
          ticketId={ticketId}
          articleId={replyTarget.id}
          replyAll={false}
          open={replyOpen}
          onClose={() => setReplyOpen(false)}
        />
      )}
      {dialog === "customer" && (
        <CustomerPickerDialog
          ticketId={ticketId}
          currentCustomerId={ticket.customer_id}
          currentCustomerUserId={ticket.customer_user_id}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === "pending" && (
        <PendingDialog
          ticketId={ticketId}
          pendingStates={pendingStates}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === "link" && <LinkDialog ticketId={ticketId} onClose={() => setDialog(null)} />}
      {dialog === "merge" && <MergeDialog ticketId={ticketId} onClose={() => setDialog(null)} />}
    </div>
  );
}

/* ── SLA chip ─────────────────────────────────────────────────────────── */

function humanDuration(
  t: (key: string, opts: { count: number }) => string,
  seconds: number,
): string {
  const abs = Math.max(0, Math.floor(seconds));
  if (abs >= 86400) return t("ticket.durationDays", { count: Math.floor(abs / 86400) });
  if (abs >= 3600) return t("ticket.durationHours", { count: Math.floor(abs / 3600) });
  return t("ticket.durationMinutes", { count: Math.max(1, Math.floor(abs / 60)) });
}

/** Humanized SLA state: "⚠ Update-SLA überfällig · 40 Tage" (breached, red)
 * or "Update-SLA in 25 Min." (approaching, amber). Replaces the raw
 * ``-973h19m`` countdown + separate badge of the previous header. */
function SlaChip({ ticket }: { ticket: TicketDetail }) {
  const { t } = useTranslation();
  const slots: { label: string; epoch: number }[] = [
    { label: t("ticket.escResponse"), epoch: ticket.escalation_response_time },
    { label: t("ticket.escUpdate"), epoch: ticket.escalation_update_time },
    { label: t("ticket.escSolution"), epoch: ticket.escalation_solution_time },
    { label: t("ticket.escalated"), epoch: ticket.escalation_time },
  ].filter((s) => s.epoch > 0);
  if (slots.length === 0) return null;

  const nowSec = Date.now() / 1000;
  const breached = slots.filter((s) => s.epoch < nowSec);
  if (breached.length > 0) {
    // Longest-overdue slot is the one that matters most.
    const worst = breached.reduce((a, b) => (a.epoch < b.epoch ? a : b));
    return (
      <Badge tone="danger" data-testid="ticket-sla-chip">
        ⚠ {t("ticket.slaOverdue", {
          label: worst.label,
          duration: humanDuration(t, nowSec - worst.epoch),
        })}
      </Badge>
    );
  }
  const next = slots.reduce((a, b) => (a.epoch < b.epoch ? a : b));
  if (escalationLevel(next.epoch) !== "approaching") return null;
  return (
    <Badge tone="warn" data-testid="ticket-sla-chip">
      {t("ticket.slaDue", { label: next.label, duration: humanDuration(t, next.epoch - nowSec) })}
    </Badge>
  );
}

/* ── Row-1 queue breadcrumb ───────────────────────────────────────────── */

function QueueBreadcrumb({
  items,
  value,
  valueLabel,
  disabledTitle,
  rootLabel,
  placeholder,
  onSelect,
}: {
  items: SelectMenuItem<number>[];
  value: number;
  valueLabel: string | null | undefined;
  disabledTitle?: string;
  rootLabel: string;
  placeholder: string;
  onSelect: (id: number) => void;
}) {
  const crumb = (
    <>
      {rootLabel} / <span className="font-medium text-ink">{valueLabel || "—"}</span>
    </>
  );
  if (disabledTitle) {
    return (
      <span data-testid="ticket-pill-queue" title={disabledTitle} className="opacity-60">
        {crumb}
      </span>
    );
  }
  return (
    <SelectMenu
      items={items}
      value={value}
      onSelect={onSelect}
      placeholder={placeholder}
      panelTestId="ticket-pill-queue-menu"
      trigger={({ ref, toggleProps }) => (
        <button
          ref={ref}
          type="button"
          data-testid="ticket-pill-queue"
          {...toggleProps}
          className="rounded px-1 py-0.5 transition-colors duration-100 hover:bg-surface-subtle hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
        >
          {crumb} <span aria-hidden>⌄</span>
        </button>
      )}
    />
  );
}

/* ── Row-3 people pills ───────────────────────────────────────────────── */

function initialsOf(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  const first = parts[0][0] ?? "";
  const last = parts.length > 1 ? (parts[parts.length - 1][0] ?? "") : "";
  return (first + last).toUpperCase() || "?";
}

/** Static shell of a people pill (avatar + "Label Name ▾"); clickable when
 * `onClick` is set, lock-tooltip via `disabledTitle` otherwise. */
function PersonShell({
  label,
  name,
  email,
  testId,
  disabledTitle,
  muted,
  avatarTone = "accent",
  onClick,
  triggerRef,
  toggleProps,
}: {
  label: string;
  name: string;
  email?: string | null;
  testId: string;
  disabledTitle?: string;
  muted?: boolean;
  avatarTone?: "accent" | "customer";
  onClick?: () => void;
  triggerRef?: React.RefObject<HTMLButtonElement | null>;
  toggleProps?: object;
}) {
  const interactive = Boolean(onClick || toggleProps);
  const inner = (
    <>
      <Avatar initials={initialsOf(name)} email={email} size={20} tone={avatarTone} />
      <span className="text-muted">{label}</span>
      <span className={cn("font-medium", muted ? "text-muted" : "text-ink")}>{name}</span>
      {interactive ? (
        <span aria-hidden className="text-muted">
          ⌄
        </span>
      ) : (
        disabledTitle && (
          <span aria-hidden className="text-[10px] text-muted">
            🔒
          </span>
        )
      )}
    </>
  );
  if (!interactive) {
    return (
      <span
        data-testid={testId}
        title={disabledTitle}
        className="inline-flex items-center gap-1.5 rounded-full border border-hairline bg-surface-subtle/60 py-0.5 pl-0.5 pr-2.5 text-xs opacity-70"
      >
        {inner}
      </span>
    );
  }
  return (
    <button
      ref={triggerRef}
      type="button"
      data-testid={testId}
      onClick={onClick}
      {...toggleProps}
      className="inline-flex items-center gap-1.5 rounded-full border border-hairline bg-surface py-0.5 pl-0.5 pr-2.5 text-xs transition-colors duration-100 hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
    >
      {inner}
    </button>
  );
}

/** People pill whose value opens a `SelectMenu` listbox (owner/responsible). */
function PersonSelect({
  label,
  name,
  testId,
  panelTestId,
  disabledTitle,
  muted,
  items,
  value,
  placeholder,
  onSelect,
}: {
  label: string;
  name: string;
  testId: string;
  panelTestId: string;
  disabledTitle?: string;
  muted?: boolean;
  items: SelectMenuItem<number>[];
  value: number | null;
  placeholder: string;
  onSelect: (value: number) => void;
}) {
  if (disabledTitle) {
    return (
      <PersonShell label={label} name={name} testId={testId} disabledTitle={disabledTitle} muted={muted} />
    );
  }
  return (
    <SelectMenu
      items={items}
      value={value}
      onSelect={onSelect}
      placeholder={placeholder}
      panelTestId={panelTestId}
      trigger={({ ref, toggleProps }) => (
        <PersonShell
          label={label}
          name={name}
          testId={testId}
          muted={muted}
          triggerRef={ref}
          toggleProps={toggleProps}
        />
      )}
    />
  );
}
