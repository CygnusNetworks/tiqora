import { useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import type { TicketDetail } from "@/lib/api";
import { useAuth } from "@/auth/AuthContext";
import { Button } from "@/components/ui/Button";
import { Menu, MenuItem, MenuLabel, MenuSeparator } from "@/components/ui/Menu";
import { PriorityChip, StateChip } from "@/components/ui/StatusChip";
import { formatDateTime } from "@/lib/format";
import { stateLabel } from "@/lib/status";
import { cn } from "@/lib/cn";
import { ReplyDialog } from "./ReplyDialog";
import { articleSortKey } from "@/lib/article";
import {
  AgentPickerDialog,
  CustomerPickerDialog,
  LinkDialog,
  MergeDialog,
  MovePickerDialog,
  PendingDialog,
} from "./ActionToolbar";
import { ticketPerms, usePatchTicket } from "@/lib/ticket";

/**
 * Header actions for the redesigned ticket-zoom view ("Variante 1 —
 * Interaktive Wert-Pills"): a wrapping row of clickable metadata pills
 * (status/priority/queue/owner/customer) that each open the SAME
 * dialogs/pickers ActionToolbar used to hang off its flat button row, plus
 * primary Antworten/Notiz/Mehr actions. ActionToolbar itself keeps owning
 * the dialog implementations — this component only re-wires their triggers.
 */
export function TicketHeaderActions({
  ticket,
  canNote,
  onOpenNote,
}: {
  ticket: TicketDetail;
  /** Whether the agent may reply / add notes (``note`` permission). */
  canNote: boolean;
  /** Opens the internal-note composer at the bottom of the article list. */
  onOpenNote: () => void;
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

  // Which modal dialog is open (null = none) — mirrors ActionToolbar's own
  // single-dialog-at-a-time state, kept separately since this is a second,
  // independent trigger surface for the same dialogs.
  const [dialog, setDialog] = useState<
    "owner" | "responsible" | "customer" | "move" | "pending" | "link" | "merge" | null
  >(null);
  const [replyOpen, setReplyOpen] = useState(false);

  const toggleWatch = () => {
    if (!user) return;
    patch.mutate(
      ticket.is_watched ? { unwatch_user_id: user.id } : { watcher_user_id: user.id },
    );
  };

  return (
    <div className="flex flex-wrap items-center gap-2 print:hidden" data-testid="ticket-header-actions">
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
        {perms.owner && (
          <MenuItem testId="more-responsible" onSelect={() => setDialog("responsible")}>
            {t("ticket.toolbar.responsible")}
          </MenuItem>
        )}
        {perms.rw && (
          <MenuItem testId="more-lock" onSelect={() => patch.mutate({ lock: isLocked ? "unlock" : "lock" })}>
            {isLocked ? t("ticket.toolbar.unlock") : t("ticket.toolbar.lock")}
          </MenuItem>
        )}
        {user && (
          <MenuItem
            testId="more-watch"
            selected={ticket.is_watched}
            onSelect={toggleWatch}
          >
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
        <MenuItem testId="more-appointment" onSelect={() => void navigate({ to: "/agent/calendar" })}>
          {t("ticket.toolbar.appointment")}
        </MenuItem>
      </Menu>

      {/* ── Metadata pills ─────────────────────────────────────────────── */}
      <div className="flex w-full flex-wrap items-center gap-1.5">
        <StatusPill label={t("ticket.state")} disabledTitle={!perms.rw ? noPerm : undefined}>
          <Menu
            align="left"
            panelTestId="ticket-pill-state-menu"
            trigger={({ ref, toggleProps }) => (
              <button
                ref={ref}
                type="button"
                data-testid="ticket-pill-state"
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
        </StatusPill>

        <StatusPill label={t("ticket.priority")} disabledTitle={!perms.priority ? noPerm : undefined}>
          <Menu
            align="left"
            panelTestId="ticket-pill-priority-menu"
            trigger={({ ref, toggleProps }) => (
              <button
                ref={ref}
                type="button"
                data-testid="ticket-pill-priority"
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
        </StatusPill>

        <Pill
          label={t("ticket.queue")}
          testId="ticket-pill-queue"
          disabledTitle={!perms.move_into ? noPerm : undefined}
          onClick={perms.move_into ? () => setDialog("move") : undefined}
        >
          {ticket.queue_name}
        </Pill>
        <Pill
          label={t("ticket.owner")}
          testId="ticket-pill-owner"
          disabledTitle={!perms.owner ? noPerm : undefined}
          onClick={perms.owner ? () => setDialog("owner") : undefined}
        >
          {ticket.owner_name || ticket.owner_login}
        </Pill>
        <Pill
          label={t("ticket.customer")}
          testId="ticket-pill-customer"
          disabledTitle={!perms.rw ? noPerm : undefined}
          onClick={perms.rw ? () => setDialog("customer") : undefined}
        >
          {ticket.customer_user_id || ticket.customer_id}
        </Pill>
        <Pill label={t("ticket.created")} dashed>
          {formatDateTime(ticket.create_time, locale)}
        </Pill>
        <Pill label={t("ticket.changed")} dashed>
          {formatDateTime(ticket.change_time, locale)}
        </Pill>
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
          pendingStates={pendingStates}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === "link" && <LinkDialog ticketId={ticketId} onClose={() => setDialog(null)} />}
      {dialog === "merge" && <MergeDialog ticketId={ticketId} onClose={() => setDialog(null)} />}
    </div>
  );
}

/** Base pill shell: tiny uppercase label above a value; clickable when `onClick` is set. */
function Pill({
  label,
  children,
  onClick,
  testId,
  disabledTitle,
  dashed,
}: {
  label: string;
  children: ReactNode;
  onClick?: () => void;
  testId?: string;
  disabledTitle?: string;
  /** Non-interactive display-only pill (Erstellt/Geändert). */
  dashed?: boolean;
}) {
  if (!children) return null;
  const inner = (
    <span className="flex flex-col items-start leading-tight">
      <span className="text-[9px] font-semibold uppercase tracking-wide text-muted">{label}</span>
      <span className="text-xs font-medium text-ink">{children}</span>
    </span>
  );
  if (!onClick) {
    return (
      <span
        data-testid={testId}
        title={disabledTitle}
        className={cn(
          "inline-flex items-center rounded-full border px-2.5 py-1",
          dashed
            ? "border-dashed border-hairline bg-transparent"
            : "border-hairline bg-surface-subtle/60",
        )}
      >
        {inner}
      </span>
    );
  }
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={testId}
      title={disabledTitle}
      className="inline-flex items-center rounded-full border border-hairline bg-surface px-2.5 py-1 transition-colors duration-100 hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
    >
      {inner}
    </button>
  );
}

/** Pill variant whose value is itself a `Menu` trigger (state/priority soft-chips). */
function StatusPill({
  label,
  disabledTitle,
  children,
}: {
  label: string;
  disabledTitle?: string;
  children: ReactNode;
}) {
  return (
    <span
      title={disabledTitle}
      className={cn(
        "inline-flex items-center gap-0 rounded-full border border-hairline bg-surface px-2.5 py-1",
        disabledTitle && "opacity-50",
      )}
    >
      <span
        className={cn(
          "flex flex-col items-start leading-tight",
          disabledTitle && "pointer-events-none",
        )}
      >
        <span className="text-[9px] font-semibold uppercase tracking-wide text-muted">{label}</span>
        {children}
      </span>
    </span>
  );
}
