import { useEffect, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import type { TicketDetail } from "@/lib/api";
import { useAuth } from "@/auth/AuthContext";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Spinner } from "@/components/ui/Spinner";
import { stateLabel } from "@/lib/status";
import { ticketPerms, usePatchTicket } from "@/lib/ticket";

const inputCls =
  "w-full rounded border border-hairline bg-surface px-2 py-1.5 text-sm text-ink placeholder:text-muted focus:outline-none focus:ring-1 focus:ring-accent";

// ── Dropdown menu primitive ────────────────────────────────────────────────

function ToolbarMenu({
  label,
  icon,
  disabled,
  children,
  testId,
}: {
  label: string;
  icon: ReactNode;
  disabled?: boolean;
  children: (close: () => void) => ReactNode;
  testId?: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return undefined;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      <ToolbarButton
        label={label}
        icon={icon}
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        testId={testId}
      />
      {open && (
        <div
          className="absolute left-0 z-20 mt-1 max-h-72 min-w-44 overflow-auto rounded-md border border-hairline bg-surface py-1 shadow-lg"
          role="menu"
        >
          {children(() => setOpen(false))}
        </div>
      )}
    </div>
  );
}

function MenuItem({
  onClick,
  active,
  children,
}: {
  onClick: () => void;
  active?: boolean;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      className={`block w-full px-3 py-1.5 text-left text-sm hover:bg-surface-subtle ${
        active ? "font-semibold text-accent" : "text-ink"
      }`}
    >
      {children}
    </button>
  );
}

function ToolbarButton({
  label,
  icon,
  onClick,
  disabled,
  active,
  testId,
  title,
}: {
  label: string;
  icon: ReactNode;
  onClick: () => void;
  disabled?: boolean;
  active?: boolean;
  testId?: string;
  /** Tooltip; shown even when disabled (via span wrapper). */
  title?: string;
}) {
  const btn = (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      data-testid={testId}
      title={disabled ? undefined : title}
      className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs font-medium transition-colors duration-100 disabled:pointer-events-none disabled:opacity-40 ${
        active
          ? "border-accent bg-accent/10 text-accent"
          : "border-hairline bg-surface text-ink hover:bg-surface-subtle"
      }`}
    >
      <span className="text-muted">{icon}</span>
      {label}
    </button>
  );
  // Disabled buttons don't fire pointer events — wrap so the title tooltip still shows.
  if (disabled && title) {
    return (
      <span className="inline-flex" title={title}>
        {btn}
      </span>
    );
  }
  return btn;
}

// ── Minimal inline icons (single-path, 14px) ───────────────────────────────

const ic = (d: string) => (
  <svg
    viewBox="0 0 24 24"
    width="14"
    height="14"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <path d={d} />
  </svg>
);
const icons = {
  priority: ic("M12 20V10 M6 20V4 M18 20v-6"),
  state: ic("M20 6L9 17l-5-5"),
  pending: ic("M12 6v6l4 2 M12 22a10 10 0 100-20 10 10 0 000 20z"),
  close: ic("M18 6L6 18 M6 6l12 12"),
  move: ic("M5 12h14 M13 6l6 6-6 6"),
  owner: ic("M20 21a8 8 0 10-16 0 M12 11a4 4 0 100-8 4 4 0 000 8z"),
  customer: ic("M17 21v-2a4 4 0 00-8 0v2 M13 7a4 4 0 11-8 0 4 4 0 018 0z M23 21v-2a4 4 0 00-3-3.87"),
  lock: ic("M19 11H5a2 2 0 00-2 2v7h18v-7a2 2 0 00-2-2z M7 11V7a5 5 0 0110 0v4"),
  unlock: ic("M19 11H5a2 2 0 00-2 2v7h18v-7a2 2 0 00-2-2z M7 11V7a5 5 0 019.9-1"),
  watch: ic("M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z M12 15a3 3 0 100-6 3 3 0 000 6z"),
  link: ic("M10 13a5 5 0 007 0l3-3a5 5 0 00-7-7l-1 1 M14 11a5 5 0 00-7 0l-3 3a5 5 0 007 7l1-1"),
  merge: ic("M6 3v12 M18 9a3 3 0 100 6 3 3 0 000-6z M6 15a3 3 0 100 6 3 3 0 000-6z M6 9a9 9 0 009 9"),
  print: ic("M6 9V2h12v7 M6 18H4a2 2 0 01-2-2v-3a2 2 0 012-2h16a2 2 0 012 2v3a2 2 0 01-2 2h-2 M6 14h12v8H6z"),
  appointment: ic("M8 2v4 M16 2v4 M3 10h18 M5 4h14a2 2 0 012 2v14a2 2 0 01-2 2H5a2 2 0 01-2-2V6a2 2 0 012-2z"),
};

// ── Main toolbar ────────────────────────────────────────────────────────────

export function ActionToolbar({ ticket }: { ticket: TicketDetail }) {
  const { t } = useTranslation();
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

  const states = statesQ.data ?? [];
  const closedStates = states.filter((s) => s.type_name.startsWith("closed"));

  const isLocked = Boolean(ticket.lock && ticket.lock.toLowerCase() !== "unlock");

  // Which dialog is open (null = none). Kept as a single value so at most one
  // dialog is mounted at a time.
  const [dialog, setDialog] = useState<
    "owner" | "responsible" | "customer" | "move" | "pending" | "link" | "merge" | null
  >(null);

  const toggleWatch = () => {
    if (!user) return;
    patch.mutate(
      ticket.is_watched ? { unwatch_user_id: user.id } : { watcher_user_id: user.id },
    );
  };

  return (
    <div
      className="flex flex-wrap items-center gap-1.5 rounded-lg border border-hairline bg-surface p-2 print:hidden"
      data-testid="action-toolbar"
    >
      {/* Priority → permission key ``priority`` */}
      <ToolbarMenu
        label={t("ticket.toolbar.priority")}
        icon={icons.priority}
        disabled={!perms.priority || prioritiesQ.isLoading}
        testId="toolbar-priority"
      >
        {(close) =>
          (prioritiesQ.data ?? []).map((p) => (
            <MenuItem
              key={p.id}
              active={p.id === ticket.priority_id}
              onClick={() => {
                patch.mutate({ priority_id: p.id });
                close();
              }}
            >
              {p.name}
            </MenuItem>
          ))
        }
      </ToolbarMenu>

      {/* State / Pending / Close → ``rw`` */}
      <ToolbarMenu
        label={t("ticket.toolbar.state")}
        icon={icons.state}
        disabled={!perms.rw || statesQ.isLoading}
        testId="toolbar-state"
      >
        {(close) =>
          states.map((s) => (
            <MenuItem
              key={s.id}
              active={s.id === ticket.state_id}
              onClick={() => {
                patch.mutate({ state_id: s.id });
                close();
              }}
            >
              {stateLabel(t, s.name)}
            </MenuItem>
          ))
        }
      </ToolbarMenu>

      <ToolbarButton
        label={t("ticket.toolbar.pending")}
        icon={icons.pending}
        disabled={!perms.rw}
        title={!perms.rw ? noPerm : undefined}
        onClick={() => setDialog("pending")}
        testId="toolbar-pending"
      />

      <ToolbarMenu
        label={t("ticket.toolbar.close")}
        icon={icons.close}
        disabled={!perms.rw || statesQ.isLoading}
        testId="toolbar-close"
      >
        {(close) =>
          closedStates.length === 0 ? (
            <div className="px-3 py-1.5 text-xs text-muted">{t("ticket.noTickets")}</div>
          ) : (
            closedStates.map((s) => (
              <MenuItem
                key={s.id}
                onClick={() => {
                  patch.mutate({ state_id: s.id });
                  close();
                }}
              >
                {stateLabel(t, s.name)}
              </MenuItem>
            ))
          )
        }
      </ToolbarMenu>

      <span className="mx-0.5 h-5 w-px bg-hairline" aria-hidden="true" />

      {/* Queue (was Move) → ``move_into`` */}
      <ToolbarButton
        label={t("ticket.toolbar.queue")}
        icon={icons.move}
        disabled={!perms.move_into}
        title={!perms.move_into ? noPerm : undefined}
        onClick={() => setDialog("move")}
        testId="toolbar-move"
      />
      {/* Owner + Responsible → ``owner`` */}
      <ToolbarButton
        label={t("ticket.toolbar.owner")}
        icon={icons.owner}
        disabled={!perms.owner}
        title={!perms.owner ? noPerm : undefined}
        onClick={() => setDialog("owner")}
        testId="toolbar-owner"
      />
      <ToolbarButton
        label={t("ticket.toolbar.responsible")}
        icon={icons.owner}
        disabled={!perms.owner}
        title={!perms.owner ? noPerm : undefined}
        onClick={() => setDialog("responsible")}
        testId="toolbar-responsible"
      />
      {/* Customer → ``rw`` */}
      <ToolbarButton
        label={t("ticket.toolbar.customer")}
        icon={icons.customer}
        disabled={!perms.rw}
        title={!perms.rw ? noPerm : undefined}
        onClick={() => setDialog("customer")}
        testId="toolbar-customer"
      />

      <span className="mx-0.5 h-5 w-px bg-hairline" aria-hidden="true" />

      {/* Lock / Unlock → ``rw`` */}
      <ToolbarButton
        label={isLocked ? t("ticket.toolbar.unlock") : t("ticket.toolbar.lock")}
        icon={isLocked ? icons.unlock : icons.lock}
        active={isLocked}
        disabled={!perms.rw}
        title={!perms.rw ? noPerm : undefined}
        onClick={() => patch.mutate({ lock: isLocked ? "unlock" : "lock" })}
        testId="toolbar-lock"
      />
      {/* Watch / Unwatch — personal, allowed regardless of write access */}
      <ToolbarButton
        label={ticket.is_watched ? t("ticket.toolbar.unwatch") : t("ticket.toolbar.watch")}
        icon={icons.watch}
        active={ticket.is_watched}
        disabled={!user}
        onClick={toggleWatch}
        testId="toolbar-watch"
      />

      <span className="mx-0.5 h-5 w-px bg-hairline" aria-hidden="true" />

      {/* Link / Merge → ``rw`` */}
      <ToolbarButton
        label={t("ticket.toolbar.link")}
        icon={icons.link}
        disabled={!perms.rw}
        title={!perms.rw ? noPerm : undefined}
        onClick={() => setDialog("link")}
        testId="toolbar-link"
      />
      <ToolbarButton
        label={t("ticket.toolbar.merge")}
        icon={icons.merge}
        disabled={!perms.rw}
        title={!perms.rw ? noPerm : undefined}
        onClick={() => setDialog("merge")}
        testId="toolbar-merge"
      />

      <span className="mx-0.5 h-5 w-px bg-hairline" aria-hidden="true" />

      {/* Print (client-side) */}
      <ToolbarButton
        label={t("ticket.toolbar.print")}
        icon={icons.print}
        onClick={() => window.print()}
        testId="toolbar-print"
      />
      {/* New appointment — the calendar has no ticket-prefill route yet, so
          this just navigates to the calendar. */}
      <ToolbarButton
        label={t("ticket.toolbar.appointment")}
        icon={icons.appointment}
        onClick={() => void navigate({ to: "/agent/calendar" })}
        testId="toolbar-appointment"
      />

      {dialog === "owner" && (
        <AgentPickerDialog
          title={t("ticket.toolbar.owner")}
          ticketId={ticketId}
          field="owner_id"
          currentId={ticket.owner_id}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === "responsible" && (
        <AgentPickerDialog
          title={t("ticket.toolbar.responsible")}
          ticketId={ticketId}
          field="responsible_id"
          currentId={ticket.responsible_user_id ?? null}
          onClose={() => setDialog(null)}
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
      {dialog === "move" && (
        <MovePickerDialog
          ticketId={ticketId}
          currentQueueId={ticket.queue_id}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === "pending" && (
        <PendingDialog
          ticketId={ticketId}
          pendingStates={states.filter((s) => s.type_name.startsWith("pending"))}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === "link" && (
        <LinkDialog ticketId={ticketId} onClose={() => setDialog(null)} />
      )}
      {dialog === "merge" && (
        <MergeDialog ticketId={ticketId} onClose={() => setDialog(null)} />
      )}
    </div>
  );
}

// ── Dialogs ─────────────────────────────────────────────────────────────────

function DialogActions({
  onCancel,
  onSave,
  disabled,
}: {
  onCancel: () => void;
  onSave: () => void;
  disabled: boolean;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex items-center justify-end gap-1.5 pt-2">
      <Button variant="ghost" size="sm" onClick={onCancel}>
        {t("ticket.dialog.cancel")}
      </Button>
      <Button variant="primary" size="sm" disabled={disabled} onClick={onSave}>
        {t("ticket.dialog.save")}
      </Button>
    </div>
  );
}

export function AgentPickerDialog({
  title,
  ticketId,
  field,
  currentId,
  onClose,
}: {
  title: string;
  ticketId: number;
  field: "owner_id" | "responsible_id";
  currentId?: number | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [agentId, setAgentId] = useState(String(currentId ?? ""));
  const agentsQ = useQuery({
    queryKey: ["reference", "agents"],
    queryFn: () => api.listReferenceAgents(),
  });
  const patch = usePatchTicket(ticketId, onClose);

  return (
    <Dialog open onClose={onClose} title={title}>
      <div className="space-y-2" data-testid="agent-picker-dialog">
        {agentsQ.isLoading ? (
          <div className="flex justify-center py-4">
            <Spinner />
          </div>
        ) : (
          <select
            className={inputCls}
            value={agentId}
            onChange={(e) => setAgentId(e.target.value)}
            data-testid="agent-picker-select"
          >
            <option value="">{t("ticket.dialog.selectPlaceholder")}</option>
            {(agentsQ.data ?? []).map((a) => (
              <option key={a.id} value={a.id}>
                {a.full_name} ({a.login})
              </option>
            ))}
          </select>
        )}
        {patch.isError && <p className="text-xs text-danger">{t("ticket.dialog.genericError")}</p>}
        <DialogActions
          onCancel={onClose}
          onSave={() => patch.mutate({ [field]: Number(agentId) })}
          disabled={!agentId || patch.isPending}
        />
      </div>
    </Dialog>
  );
}

export function CustomerPickerDialog({
  ticketId,
  currentCustomerId,
  currentCustomerUserId,
  onClose,
}: {
  ticketId: number;
  currentCustomerId?: string | null;
  currentCustomerUserId?: string | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [q, setQ] = useState(currentCustomerUserId ?? "");
  const customersQ = useQuery({
    queryKey: ["reference", "customers", q],
    queryFn: () => api.searchReferenceCustomers({ q }),
    enabled: q.trim().length >= 2,
  });
  const patch = usePatchTicket(ticketId, onClose);
  const hasCurrent = Boolean(currentCustomerUserId || currentCustomerId);

  return (
    <Dialog open onClose={onClose} title={t("ticket.toolbar.customer")}>
      <div className="space-y-2" data-testid="customer-picker-dialog">
        {hasCurrent && (
          <div
            className="flex items-center gap-2 rounded border border-hairline bg-surface-subtle px-3 py-1.5 text-sm"
            data-testid="customer-picker-current"
          >
            <span className="text-muted">{t("ticket.dialog.currentCustomer")}:</span>
            <span className="font-medium text-ink">
              {currentCustomerUserId || "—"}
            </span>
            {currentCustomerId ? (
              <Badge tone="muted" className="ml-auto shrink-0 rounded-full">
                {currentCustomerId}
              </Badge>
            ) : null}
          </div>
        )}
        <input
          className={inputCls}
          value={q}
          autoFocus
          placeholder={t("ticket.dialog.selectPlaceholder")}
          onChange={(e) => setQ(e.target.value)}
        />
        <div className="max-h-56 overflow-auto rounded border border-hairline">
          {customersQ.isLoading ? (
            <div className="flex justify-center py-3">
              <Spinner />
            </div>
          ) : (customersQ.data ?? []).length === 0 ? (
            <div className="px-3 py-2 text-xs text-muted">{t("ticket.noTickets")}</div>
          ) : (
            (customersQ.data ?? []).map((c) => (
              <button
                key={c.login}
                type="button"
                onClick={() =>
                  patch.mutate({ customer_user_id: c.login, customer_id: c.customer_id })
                }
                disabled={patch.isPending}
                data-testid={`customer-picker-result-${c.login}`}
                className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm text-ink hover:bg-surface-subtle disabled:opacity-50"
              >
                <span className="min-w-0 flex-1 truncate">
                  <span className="font-medium">{c.full_name}</span>{" "}
                  <span className="text-muted">{c.email}</span>
                </span>
                {c.customer_id ? (
                  <Badge
                    tone="muted"
                    className="ml-auto shrink-0 rounded-full"
                    data-testid={`customer-picker-id-${c.login}`}
                    title={t("ticket.toolbar.customerNumber")}
                  >
                    {c.customer_id}
                  </Badge>
                ) : null}
              </button>
            ))
          )}
        </div>
        {patch.isError && <p className="text-xs text-danger">{t("ticket.dialog.genericError")}</p>}
      </div>
    </Dialog>
  );
}

type FlatQueue = { id: number; name: string };
function flattenQueues(
  nodes: { id: number; name: string; children?: unknown }[],
): FlatQueue[] {
  const out: FlatQueue[] = [];
  const walk = (list: { id: number; name: string; children?: unknown }[]) => {
    for (const n of list) {
      out.push({ id: n.id, name: n.name });
      if (Array.isArray(n.children)) walk(n.children as typeof list);
    }
  };
  walk(nodes);
  return out;
}

export function MovePickerDialog({
  ticketId,
  currentQueueId,
  onClose,
}: {
  ticketId: number;
  currentQueueId?: number | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [queueId, setQueueId] = useState(String(currentQueueId ?? ""));
  const queuesQ = useQuery({ queryKey: ["queues"], queryFn: () => api.listQueues() });
  const queues = flattenQueues(queuesQ.data ?? []);
  const patch = usePatchTicket(ticketId, onClose);

  return (
    <Dialog open onClose={onClose} title={t("ticket.toolbar.queue")}>
      <div className="space-y-2" data-testid="move-picker-dialog">
        <select
          className={inputCls}
          value={queueId}
          onChange={(e) => setQueueId(e.target.value)}
          data-testid="queue-picker-select"
        >
          <option value="">{t("ticket.dialog.selectPlaceholder")}</option>
          {queues.map((qu) => (
            <option key={qu.id} value={qu.id}>
              {qu.name}
            </option>
          ))}
        </select>
        {patch.isError && <p className="text-xs text-danger">{t("ticket.dialog.genericError")}</p>}
        <DialogActions
          onCancel={onClose}
          onSave={() => patch.mutate({ queue_id: Number(queueId) })}
          disabled={!queueId || patch.isPending}
        />
      </div>
    </Dialog>
  );
}

export function PendingDialog({
  ticketId,
  pendingStates,
  onClose,
}: {
  ticketId: number;
  pendingStates: { id: number; name: string }[];
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [stateId, setStateId] = useState(
    pendingStates.length > 0 ? String(pendingStates[0].id) : "",
  );
  const [when, setWhen] = useState("");
  const patch = usePatchTicket(ticketId, onClose);

  return (
    <Dialog open onClose={onClose} title={t("ticket.toolbar.pending")}>
      <div className="space-y-2" data-testid="pending-dialog">
        <label className="block text-xs text-muted">
          {t("ticket.toolbar.state")}
          <select
            className={inputCls}
            value={stateId}
            onChange={(e) => setStateId(e.target.value)}
          >
            <option value="">{t("ticket.dialog.selectPlaceholder")}</option>
            {pendingStates.map((s) => (
              <option key={s.id} value={s.id}>
                {stateLabel(t, s.name)}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-xs text-muted">
          {t("ticket.dialog.apptStart")}
          <input
            type="datetime-local"
            className={inputCls}
            value={when}
            onChange={(e) => setWhen(e.target.value)}
          />
        </label>
        {patch.isError && <p className="text-xs text-danger">{t("ticket.dialog.genericError")}</p>}
        <DialogActions
          onCancel={onClose}
          onSave={() =>
            patch.mutate({
              state_id: Number(stateId),
              pending_time: when ? new Date(when).toISOString() : null,
            })
          }
          disabled={!stateId || !when || patch.isPending}
        />
      </div>
    </Dialog>
  );
}

const LINK_TYPES = ["Normal", "ParentChild"] as const;

export function LinkDialog({
  ticketId,
  onClose,
}: {
  ticketId: number;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [targetId, setTargetId] = useState("");
  const [linkType, setLinkType] = useState<(typeof LINK_TYPES)[number]>("Normal");

  const linksQ = useQuery({
    queryKey: ["tickets", ticketId, "links"],
    queryFn: () => api.listTicketLinks(ticketId),
  });

  const create = useMutation({
    mutationFn: () =>
      api.createTicketLink(ticketId, {
        target_ticket_id: Number(targetId),
        link_type: linkType,
      }),
    onSuccess: () => {
      setTargetId("");
      void qc.invalidateQueries({ queryKey: ["tickets", ticketId, "links"] });
      void qc.invalidateQueries({ queryKey: ["tickets", ticketId] });
    },
  });

  return (
    <Dialog open onClose={onClose} title={t("ticket.toolbar.link")}>
      <div className="space-y-2" data-testid="link-dialog">
        {(linksQ.data ?? []).length > 0 && (
          <ul className="max-h-32 overflow-auto rounded border border-hairline text-sm">
            {(linksQ.data ?? []).map((l) => (
              <li
                key={l.other_ticket_id}
                className="border-b border-hairline px-3 py-1.5 last:border-0"
              >
                <span className="font-mono text-accent">{l.other_tn ?? l.other_ticket_id}</span>{" "}
                <span className="text-muted">{l.other_title}</span>
              </li>
            ))}
          </ul>
        )}
        <label className="block text-xs text-muted">
          {t("ticket.dialog.linkTargetId")}
          <input
            className={inputCls}
            value={targetId}
            inputMode="numeric"
            onChange={(e) => setTargetId(e.target.value)}
          />
        </label>
        <label className="block text-xs text-muted">
          {t("ticket.dialog.linkType")}
          <select
            className={inputCls}
            value={linkType}
            onChange={(e) => setLinkType(e.target.value as (typeof LINK_TYPES)[number])}
          >
            {LINK_TYPES.map((lt) => (
              <option key={lt} value={lt}>
                {lt}
              </option>
            ))}
          </select>
        </label>
        {create.isError && <p className="text-xs text-danger">{t("ticket.dialog.genericError")}</p>}
        <DialogActions
          onCancel={onClose}
          onSave={() => create.mutate()}
          disabled={!targetId || create.isPending}
        />
      </div>
    </Dialog>
  );
}

export function MergeDialog({
  ticketId,
  onClose,
}: {
  ticketId: number;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [mainId, setMainId] = useState("");

  const merge = useMutation({
    mutationFn: () => api.mergeTicket(ticketId, { main_ticket_id: Number(mainId) }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["tickets", ticketId] });
      void qc.invalidateQueries({ queryKey: ["tickets", ticketId, "articles"] });
      onClose();
    },
  });

  return (
    <Dialog open onClose={onClose} title={t("ticket.toolbar.merge")}>
      <div className="space-y-2" data-testid="merge-dialog">
        <label className="block text-xs text-muted">
          {t("ticket.dialog.mergeTargetId")}
          <input
            className={inputCls}
            value={mainId}
            inputMode="numeric"
            autoFocus
            onChange={(e) => setMainId(e.target.value)}
          />
        </label>
        {merge.isError && <p className="text-xs text-danger">{t("ticket.dialog.genericError")}</p>}
        <DialogActions
          onCancel={onClose}
          onSave={() => merge.mutate()}
          disabled={!mainId || merge.isPending}
        />
      </div>
    </Dialog>
  );
}
