import { useEffect, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { toBcp47 } from "@/i18n";
import { api } from "@/lib/api";
import type { TicketDetail } from "@/lib/api";
import { useAuth } from "@/auth/AuthContext";
import { Avatar } from "@/components/ui/Avatar";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ExternalLinkIcon, MoreIcon, PhoneIcon, UserIcon } from "@/components/ui/icons";
import { Menu, MenuItem, MenuLabel, MenuSeparator } from "@/components/ui/Menu";
import { SelectMenu, type SelectMenuItem } from "@/components/ui/SelectMenu";
import { PriorityChip } from "@/components/ui/StatusChip";
import { formatDateTime } from "@/lib/format";
import { escalationLevel, humanDuration, stateLabel } from "@/lib/status";
import { cn } from "@/lib/cn";
import { useReplyDraft } from "@/lib/replyDrafts";
import { channelNameOf, dominantChannel } from "@/lib/articleChannel";
import { requestComposer, requestConversationView } from "./telegram/composerBus";
import { ReplyDialog } from "./ReplyDialog";
import { TicketMetaCounters } from "./TicketMetaCounters";
import { articleSortKey } from "@/lib/article";
import { flattenQueues } from "./QueueTree";
import { CustomerPickerDialog, LinkDialog, MergeDialog, PendingDialog } from "./ActionToolbar";
import { ticketPerms, usePatchTicket } from "@/lib/ticket";
import { stateColorVar } from "@/lib/status";
import type { TicketAiSlots } from "./AiPanel";
import { TicketBackNav } from "./TicketBackNav";
import { PhoneCallDialog } from "./phone/PhoneCallDialog";
import { phoneApi, type PhoneDirection } from "@/lib/phoneApi";
import {
  consumePhoneCallRequest,
  dialHref,
  peekPhoneCallRequest,
  subscribePhoneCallRequests,
  type PhoneCallRequestIntent,
} from "@/lib/phoneCall";

/**
 * Ticket-zoom header ("3b"). Rows:
 *
 *   1. back breadcrumb to the list the ticket was opened from, ‹ › within it
 *   2. title with the AI summary as subtitle; Antworten (+ AI drafts),
 *      Notiz and one ⋯ menu on the right
 *   3. AI banners that need attention (paused, hand-over, triage) — only if present
 *   4. status bar (Neu · Offen · Wartend ⌄ · Geschlossen ⌄); priority,
 *      type/service/SLA pickers and the SLA chip on the right
 *   5. queue, people, customer and similar tickets; counters + timestamp right
 *
 * All values stay clickable dropdowns/dialogs; ActionToolbar keeps owning
 * the dialog implementations — this component only re-wires their triggers.
 */
const headerLinkButtonClass =
  "inline-flex items-center gap-1 rounded-md border border-hairline bg-surface px-2 py-1 text-xs font-medium text-ink transition-colors duration-100 hover:bg-surface-subtle";

export function TicketHeaderActions({
  ticket,
  canNote,
  onOpenNote,
  overflowItems,
  ai,
  similar,
}: {
  ticket: TicketDetail;
  /** Whether the agent may reply / add notes (``note`` permission). */
  canNote: boolean;
  /** Opens the internal-note composer at the bottom of the article list. */
  onOpenNote: () => void;
  /** Page-level items (history tab, sort, process …) appended to the ⋯ menu. */
  overflowItems?: ReactNode;
  /** AI pieces placed into the header (summary subtitle, drafts, banners). */
  ai?: TicketAiSlots;
  /** "Similar tickets" trigger for the people row. */
  similar?: ReactNode;
}) {
  const { t, i18n } = useTranslation();
  const locale = toBcp47(i18n.language);
  const { user } = useAuth();
  const navigate = useNavigate();
  const ticketId = ticket.id;
  const perms = ticketPerms(ticket);
  const noPerm = t("ticket.toolbar.noPermission");
  const patch = usePatchTicket(ticketId);

  // Base reference lists (full catalog) + ACL filter map for this ticket.
  // Znuny TicketACL reduces pickers to Possible/PossibleNot without replacing
  // group/role queue permissions.
  const prioritiesQ = useQuery({
    queryKey: ["reference", "priorities"],
    queryFn: () => api.listReferencePriorities(),
  });
  const typesQ = useQuery({
    queryKey: ["reference", "types"],
    queryFn: () => api.listReferenceTypes(),
  });
  const servicesQ = useQuery({
    queryKey: ["reference", "services"],
    queryFn: () => api.listReferenceServices(),
  });
  const slasQ = useQuery({
    queryKey: ["reference", "slas", ticket.service_id ?? null],
    queryFn: () =>
      api.listReferenceSlas(
        ticket.service_id ? { service_id: ticket.service_id } : {},
      ),
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
  const aclFieldsQ = useQuery({
    queryKey: ["tickets", ticketId, "field-options", "AgentTicketZoom"],
    queryFn: () =>
      api.ticketAclFieldOptions(ticketId, {
        fields: "state,priority,type,service,sla,queue",
        action: "AgentTicketZoom",
      }),
  });
  // Latest article drives the header's "Antworten" shortcut — same target a
  // customer-visible per-article reply would pick, so it stays cheap to
  // find rather than adding a dedicated endpoint.
  const articlesQ = useQuery({
    queryKey: ["tickets", ticketId, "articles"],
    queryFn: () => api.listArticles(ticketId),
  });
  // Resolved external customer-tool link (second header button, admin
  // Section "Externe Kunden-Links"); server returns url: null when no
  // per-queue config applies, so this never 404s.
  const customerLinkQ = useQuery({
    queryKey: ["tickets", ticketId, "customer-link"],
    queryFn: ({ signal }) => api.getTicketCustomerLink(ticketId, signal),
    enabled: ticketId > 0,
  });

  const aclAllowed = (field: string, id: number): boolean => {
    const map = aclFieldsQ.data?.[field];
    // Until ACL options load (or if ACL returned empty for a field), keep full list.
    if (!map || Object.keys(map).length === 0) return true;
    return String(id) in map || id in (map as Record<number, string>);
  };

  const states = (statesQ.data ?? []).filter((s) => aclAllowed("state", s.id));
  const closedStates = states.filter((s) => s.type_name.startsWith("closed"));
  const pendingStates = states.filter((s) => s.type_name.startsWith("pending"));
  const primaryStates = states.filter(
    (s) => !s.type_name.startsWith("closed") && !s.type_name.startsWith("pending"),
  );
  const priorities = (prioritiesQ.data ?? []).filter((p) => aclAllowed("priority", p.id));
  const types = (typesQ.data ?? []).filter((ty) => aclAllowed("type", ty.id));
  const services = (servicesQ.data ?? []).filter((s) => aclAllowed("service", s.id));
  const slas = (slasQ.data ?? []).filter((s) => aclAllowed("sla", s.id));

  const articles = articlesQ.data ?? [];
  const visibleArticles = articles.filter((a) => a.is_visible_for_customer);
  const replyTarget = [...(visibleArticles.length > 0 ? visibleArticles : articles)].sort(
    (a, b) => articleSortKey(b) - articleSortKey(a),
  )[0];
  // Telegram tickets reply through the messenger-style chat composer, not
  // the email-style dialog — see composerBus.ts.
  const isTelegramTicket = dominantChannel(articles) === "Telegram";

  const isLocked = Boolean(ticket.lock && ticket.lock.toLowerCase() !== "unlock");

  const queueItems: SelectMenuItem<number>[] = flattenQueues(queuesQ.data ?? [])
    .filter((q) => q.valid && aclAllowed("queue", q.id))
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
  // `ticket.customer_user_id` is a plain string column without a foreign key,
  // so it can hold an address that matches no customer_user row (typical for
  // tickets created from inbound mail). The backend only fills
  // `customer_email` when the login actually resolved, which makes it the
  // discriminator for "properly assigned".
  const customerResolved = Boolean(ticket.customer_user_id && ticket.customer_email);
  const customerUnresolved = Boolean(ticket.customer_user_id) && !ticket.customer_email;

  // Which modal dialog is open (null = none) — mirrors ActionToolbar's own
  // single-dialog-at-a-time state, kept separately since this is a second,
  // independent trigger surface for the same dialogs.
  const [dialog, setDialog] = useState<"customer" | "pending" | "link" | "merge" | null>(null);
  const [replyOpen, setReplyOpen] = useState(false);
  // Phone-call dialog: opened from the "Anruf" menu, by click-to-call, or by
  // a request left for this ticket before navigating here (caller lookup).
  const [phoneCall, setPhoneCall] = useState<PhoneCallRequestIntent | null>(() =>
    peekPhoneCallRequest(ticketId),
  );
  useEffect(() => consumePhoneCallRequest(ticketId), [ticketId]);
  // A request for this very ticket while it is open (CTI popup action).
  useEffect(
    () =>
      subscribePhoneCallRequests((id) => {
        if (id !== ticketId) return;
        setPhoneCall(peekPhoneCallRequest(id));
        consumePhoneCallRequest(id);
      }),
    [ticketId],
  );
  const customerQ = useQuery({
    queryKey: ["customers", ticket.customer_user_id],
    queryFn: () => api.getCustomer(ticket.customer_user_id as string),
    enabled: customerResolved && perms.rw,
  });
  const phoneConfigQ = useQuery({
    queryKey: ["reference", "phone-config"],
    queryFn: () => phoneApi.phoneConfig(),
    enabled: perms.rw,
    staleTime: 10 * 60 * 1000,
  });
  const customerNumbers = [
    { kind: "phone" as const, number: customerQ.data?.phone },
    { kind: "mobile" as const, number: customerQ.data?.mobile },
  ].filter((n): n is { kind: "phone" | "mobile"; number: string } => Boolean(n.number?.trim()));
  const openPhoneCall = (direction: PhoneDirection, number?: string) =>
    setPhoneCall({ direction, number: number ?? null });
  // Only badge the header button when the draft belongs to the article this
  // button actually opens — a draft on some other article is advertised by
  // the placeholder in the article view, not here.
  const headerHasDraft = Boolean(useReplyDraft(ticketId, replyTarget?.id ?? -1));

  const toggleWatch = () => {
    if (!user) return;
    patch.mutate(
      ticket.is_watched ? { unwatch_user_id: user.id } : { watcher_user_id: user.id },
    );
  };

  const stateType = (ticket.state_type ?? "").toLowerCase();
  const inPending = stateType.startsWith("pending");
  const inClosed = stateType.startsWith("closed");

  return (
    <div className="space-y-3 print:hidden" data-testid="ticket-header-actions">
      {/* ── Row 1: back to the originating list, ‹ › within it ─────────── */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="min-w-0 flex-1">
          <TicketBackNav ticket={ticket} />
        </div>
        {isLocked && <Badge tone="warn">{ticket.lock}</Badge>}
      </div>

      {/* ── Row 2: title + AI summary subtitle, actions right ──────────── */}
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 md:flex-nowrap">
        <div className="min-w-0 flex-1 space-y-1">
          <h1 className="font-display text-xl font-semibold leading-tight text-ink">
            {ticket.title || t("ticket.noTitle")}
          </h1>
          {ai?.summaryLine}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <span title={!canNote ? noPerm : undefined} className="inline-flex items-stretch">
            <Button
              variant="primary"
              size="sm"
              disabled={!canNote || !replyTarget}
              data-testid="ticket-actions-reply"
              data-has-draft={headerHasDraft ? "true" : undefined}
              className={cn(ai?.draftsButton && "rounded-r-none")}
              onClick={() => {
                if (isTelegramTicket) {
                  // Bring a manually-split ticket back to the conversation
                  // view first — the composer only mounts there, and the
                  // focus request is buffered until it does.
                  requestConversationView(ticketId);
                  requestComposer(ticketId, { focus: true });
                } else {
                  setReplyOpen(true);
                }
              }}
            >
              ↩ {headerHasDraft ? t("ticket.draftResume") : t("ticket.reply")}
              {headerHasDraft && (
                <span
                  aria-hidden
                  data-testid="ticket-actions-reply-dot"
                  className="h-1.5 w-1.5 shrink-0 rounded-full bg-current"
                />
              )}
            </Button>
            {ai?.draftsButton}
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
          {perms.rw && (
            <Menu
              align="right"
              panelTestId="ticket-actions-phone-menu"
              trigger={({ ref, toggleProps }) => (
                <button
                  ref={ref}
                  type="button"
                  data-testid="ticket-actions-phone"
                  {...toggleProps}
                  className="inline-flex items-center gap-1 rounded border border-hairline bg-surface px-2 py-1 text-xs text-ink transition-colors duration-100 hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
                >
                  <PhoneIcon className="h-3.5 w-3.5" /> {t("phone.button")} ⌄
                </button>
              )}
            >
              <MenuLabel>{t("phone.menuLabel")}</MenuLabel>
              <MenuItem testId="phone-menu-inbound" onSelect={() => openPhoneCall("inbound")}>
                ↙ {t("phone.inbound")}
              </MenuItem>
              <MenuItem testId="phone-menu-outbound" onSelect={() => openPhoneCall("outbound")}>
                ↗ {t("phone.outbound")}
              </MenuItem>
              {customerNumbers.length > 0 && (
                <>
                  <MenuSeparator />
                  <MenuLabel>{t("phone.callCustomer")}</MenuLabel>
                  {customerNumbers.map((n) => (
                    <a
                      key={n.kind}
                      href={dialHref(n.number, phoneConfigQ.data?.dial_scheme)}
                      data-testid={`phone-dial-${n.kind}`}
                      onClick={() => openPhoneCall("outbound", n.number)}
                      className="flex items-center justify-between gap-3 rounded px-2 py-1.5 text-sm text-ink hover:bg-surface-subtle"
                    >
                      <span className="text-xs text-muted">{t(`phone.${n.kind}Label`)}</span>
                      <span className="font-mono text-[12.5px]">{n.number}</span>
                    </a>
                  ))}
                </>
              )}
            </Menu>
          )}
          <Menu
            align="right"
            panelTestId="ticket-actions-more-menu"
            trigger={({ ref, toggleProps }) => (
              <button
                ref={ref}
                type="button"
                data-testid="ticket-actions-more"
                aria-label={t("ticket.moreActions")}
                title={t("ticket.moreActions")}
                className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-hairline bg-surface text-muted transition-colors duration-100 hover:bg-surface-subtle hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
                {...toggleProps}
              >
                <MoreIcon className="h-4 w-4" />
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
            <MenuItem
              testId="more-print"
              onSelect={() => {
                window.open(api.ticketPrintUrl(ticketId), "_blank", "noopener,noreferrer");
              }}
            >
              {t("ticket.toolbar.print")}
            </MenuItem>
            <MenuItem
              testId="more-appointment"
              onSelect={() => void navigate({ to: "/agent/calendar" })}
            >
              {t("ticket.toolbar.appointment")}
            </MenuItem>
            {overflowItems && (
              <>
                <MenuSeparator />
                {overflowItems}
              </>
            )}
          </Menu>
        </div>
      </div>
      {ai?.summaryPanel}

      {/* ── Row 3: AI decisions (hand-over, triage) ────────────────────── */}
      {ai?.banners}

      {/* ── Row 4: status bar; priority / type / service / SLA right ───── */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div
          role="group"
          aria-label={t("ticket.state")}
          title={!perms.rw ? noPerm : undefined}
          className={cn(
            "inline-flex flex-wrap overflow-hidden rounded-lg border border-hairline bg-surface-subtle",
            !perms.rw && "opacity-60",
          )}
          data-testid="ticket-status-bar"
        >
          {primaryStates.map((s) => (
            <StatusSegment
              key={s.id}
              testId={`ticket-status-${s.id}`}
              color={stateColorVar(s.name)}
              pressed={s.id === ticket.state_id}
              disabled={!perms.rw}
              onClick={() => s.id !== ticket.state_id && patch.mutate({ state_id: s.id })}
            >
              {stateLabel(t, s.name)}
            </StatusSegment>
          ))}
          {pendingStates.length > 0 && (
            <StatusSegment
              testId="ticket-status-pending"
              color="var(--color-state-pending)"
              pressed={inPending}
              disabled={!perms.rw}
              onClick={() => setDialog("pending")}
            >
              {inPending ? stateLabel(t, ticket.state) : t("ticket.stateGroup.pending")} ⌄
            </StatusSegment>
          )}
          {closedStates.length > 0 && (
            <Menu
              align="left"
              panelTestId="ticket-status-closed-menu"
              trigger={({ ref, toggleProps }) => (
                <StatusSegment
                  testId="ticket-status-closed"
                  color="var(--color-state-closed)"
                  pressed={inClosed}
                  disabled={!perms.rw}
                  buttonRef={ref}
                  toggleProps={toggleProps}
                >
                  {inClosed ? stateLabel(t, ticket.state) : t("ticket.stateGroup.closed")} ⌄
                </StatusSegment>
              )}
            >
              <MenuLabel>{t("ticket.toolbar.close")}</MenuLabel>
              {closedStates.map((s) => (
                <MenuItem
                  key={s.id}
                  testId={`ticket-status-close-${s.id}`}
                  selected={s.id === ticket.state_id}
                  onSelect={() => patch.mutate({ state_id: s.id })}
                >
                  {stateLabel(t, s.name)}
                </MenuItem>
              ))}
            </Menu>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {/* Type/Service/SLA pickers are only worth a slot once the system
              actually offers a choice — with ≤1 defined system-wide there's
              nothing to pick, so each hides independently. */}
          {(typesQ.data?.length ?? 0) > 1 && (
            <HeaderPickMenu
              testId="ticket-pill-type"
              panelTestId="ticket-pill-type-menu"
              label={ticket.type_name || t("ticket.type")}
              heading={t("ticket.type")}
              disabledTitle={!perms.rw ? noPerm : undefined}
              items={types.map((ty) => ({ id: ty.id, name: ty.name }))}
              selectedId={ticket.type_id}
              onSelect={(id) => patch.mutate({ type_id: id })}
            />
          )}
          {(servicesQ.data?.length ?? 0) > 1 && (
            <HeaderPickMenu
              testId="ticket-pill-service"
              panelTestId="ticket-pill-service-menu"
              label={ticket.service_name || t("ticket.service")}
              heading={t("ticket.service")}
              disabledTitle={!perms.rw ? noPerm : undefined}
              items={services.map((sv) => ({ id: sv.id, name: sv.name }))}
              selectedId={ticket.service_id}
              onClear={() => patch.mutate({ clear_service: true })}
              onSelect={(id) => patch.mutate({ service_id: id })}
            />
          )}
          {(slasQ.data?.length ?? 0) > 1 && (
            <HeaderPickMenu
              testId="ticket-pill-sla"
              panelTestId="ticket-pill-sla-menu"
              label={ticket.sla_name || t("ticket.sla")}
              heading={t("ticket.sla")}
              disabledTitle={!perms.rw ? noPerm : undefined}
              items={slas.map((sl) => ({ id: sl.id, name: sl.name }))}
              selectedId={ticket.sla_id}
              onClear={() => patch.mutate({ clear_sla: true })}
              onSelect={(id) => patch.mutate({ sla_id: id })}
            />
          )}
          <span
            title={!perms.priority ? noPerm : undefined}
            className={cn(!perms.priority && "opacity-60")}
          >
            <Menu
              align="right"
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
              {priorities.map((p) => (
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
        </div>
      </div>

      {/* ── Row 5: queue, people, customer; counters + timestamp right ─── */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 border-t border-hairline pt-2.5">
        <QueueBreadcrumb
          items={queueItems}
          value={ticket.queue_id}
          valueLabel={ticket.queue_name}
          disabledTitle={!perms.move_into ? noPerm : undefined}
          rootLabel={t("ticket.queue")}
          placeholder={t("ticket.dialog.selectPlaceholder")}
          onSelect={(id) => patch.mutate({ queue_id: id })}
        />
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
          email={ticket.customer_email}
          avatarTone="customer"
          testId="ticket-pill-customer"
          disabledTitle={!perms.rw ? noPerm : undefined}
          muted={!ticket.customer_user_id}
          unresolvedTitle={customerUnresolved ? t("ticket.customerUnresolved") : undefined}
          onClick={perms.rw ? () => setDialog("customer") : undefined}
        />
        {/* Only link to the customer centre when the login actually resolves to
            a customer_user record — `customer_user_id` is a free-text column
            with no FK, so mail ingest can leave a raw address in it that would
            dead-end the detail page on a 404. */}
        {customerResolved && ticket.customer_user_id && (
          <Link
            to="/agent/customers/$login"
            params={{ login: ticket.customer_user_id }}
            className={headerLinkButtonClass}
            data-testid="ticket-customer-centre-link"
            title={t("customerCentre.title")}
          >
            <UserIcon className="text-[13px]" />
            {t("customerCentre.open")}
          </Link>
        )}
        {/* Second, per-queue-configurable button pointing at an external
            customer-management tool (admin Section "Externe Kunden-Links").
            Absent entirely when the queue has no config, or visibility
            hides it from this (non-admin) agent — resolved server-side. */}
        {customerLinkQ.data?.url && (
          <a
            href={customerLinkQ.data.url}
            target="_blank"
            rel="noopener noreferrer"
            className={headerLinkButtonClass}
            data-testid="ticket-customer-external-link"
            title={customerLinkQ.data.label || t("customerCentre.externalDefault")}
          >
            <ExternalLinkIcon className="text-[13px]" />
            {customerLinkQ.data.label || t("customerCentre.externalDefault")}
          </a>
        )}
        {similar}
        {/* Counters and timestamp share the right edge: state first, then when. */}
        <span className="ml-auto inline-flex items-center gap-2">
          <TicketMetaCounters ticketId={ticketId} />
          <span
            className="inline-flex items-center gap-1 font-mono text-[11px] tabular-nums text-muted"
            data-testid="ticket-header-timestamps"
            title={`${t("ticket.created")}: ${formatDateTime(ticket.create_time, locale)} · ${t("ticket.changed")}: ${formatDateTime(ticket.change_time, locale)}`}
          >
            {formatDateTime(ticket.change_time, locale)}
          </span>
        </span>
      </div>

      {replyTarget && (
        <ReplyDialog
          ticketId={ticketId}
          articleId={replyTarget.id}
          replyAll={false}
          open={replyOpen}
          onClose={() => setReplyOpen(false)}
          channelName={channelNameOf(replyTarget)}
        />
      )}
      {phoneCall && (
        <PhoneCallDialog
          key={`${phoneCall.direction}-${phoneCall.startedAt ?? ""}-${phoneCall.endedAt ?? ""}`}
          ticket={ticket}
          initialDirection={phoneCall.direction}
          callerNumber={phoneCall.number}
          startedAt={phoneCall.startedAt}
          endedAt={phoneCall.endedAt}
          onClose={() => setPhoneCall(null)}
        />
      )}
      {ai?.overlays}
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

/* ── Status bar segment ───────────────────────────────────────────────── */

/** One segment of the header's status bar. Also serves as a `Menu` trigger
 * (closed states), hence the optional ref / toggle props. */
function StatusSegment({
  children,
  color,
  pressed,
  disabled,
  testId,
  onClick,
  buttonRef,
  toggleProps,
}: {
  children: ReactNode;
  color: string;
  pressed: boolean;
  disabled?: boolean;
  testId: string;
  onClick?: () => void;
  buttonRef?: React.RefObject<HTMLButtonElement | null>;
  toggleProps?: object;
}) {
  return (
    <button
      ref={buttonRef}
      type="button"
      data-testid={testId}
      aria-pressed={pressed}
      disabled={disabled}
      onClick={onClick}
      {...toggleProps}
      style={{ "--seg": color } as React.CSSProperties}
      className={cn(
        "inline-flex items-center gap-1.5 border-r border-hairline px-3 py-1.5 text-[12.5px] transition-colors duration-100 last:border-r-0 focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent disabled:cursor-not-allowed",
        pressed
          ? "bg-[color-mix(in_srgb,var(--seg)_14%,transparent)] font-semibold text-[var(--seg)]"
          : "text-muted enabled:hover:bg-surface enabled:hover:text-ink",
      )}
    >
      <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-[var(--seg)]" />
      {children}
    </button>
  );
}

/* ── Type / service / SLA picker ──────────────────────────────────────── */

function HeaderPickMenu({
  testId,
  panelTestId,
  label,
  heading,
  disabledTitle,
  items,
  selectedId,
  onSelect,
  onClear,
}: {
  testId: string;
  panelTestId: string;
  label: string;
  heading: string;
  disabledTitle?: string;
  items: { id: number; name: string }[];
  selectedId: number | null | undefined;
  onSelect: (id: number) => void;
  /** Offers a "—" entry that clears the value. */
  onClear?: () => void;
}) {
  return (
    <span title={disabledTitle} className={cn(disabledTitle && "opacity-60")}>
      <Menu
        align="right"
        panelTestId={panelTestId}
        trigger={({ ref, toggleProps }) => (
          <button
            ref={ref}
            type="button"
            data-testid={testId}
            disabled={Boolean(disabledTitle)}
            {...toggleProps}
            className="rounded-md border border-hairline bg-surface px-2 py-0.5 text-xs text-ink transition-colors hover:bg-surface-subtle"
          >
            {label} <span aria-hidden className="text-muted">⌄</span>
          </button>
        )}
      >
        <MenuLabel>{heading}</MenuLabel>
        {onClear && (
          <MenuItem selected={selectedId == null} onSelect={onClear}>
            —
          </MenuItem>
        )}
        {items.map((it) => (
          <MenuItem key={it.id} selected={it.id === selectedId} onSelect={() => onSelect(it.id)}>
            {it.name}
          </MenuItem>
        ))}
      </Menu>
    </span>
  );
}

/* ── SLA chip ─────────────────────────────────────────────────────────── */

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

/* ── Queue picker (people row) ────────────────────────────────────────── */

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
      <span className="text-muted">{rootLabel}</span>{" "}
      <span className="font-medium text-ink">{valueLabel || "—"}</span>
    </>
  );
  if (disabledTitle) {
    return (
      <span data-testid="ticket-pill-queue" title={disabledTitle} className="text-xs opacity-60">
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
          className="rounded-md px-1.5 py-0.5 text-xs transition-colors duration-100 hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
        >
          {crumb} <span aria-hidden className="text-muted">⌄</span>
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
  unresolvedTitle,
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
  /** Set when the value does not resolve to a real record — renders a warning
   * marker carrying this text, rather than presenting the value as a link-worthy
   * entity. */
  unresolvedTitle?: string;
  avatarTone?: "accent" | "customer";
  onClick?: () => void;
  triggerRef?: React.RefObject<HTMLButtonElement | null>;
  toggleProps?: object;
}) {
  const interactive = Boolean(onClick || toggleProps);
  const inner = (
    <>
      <span className="text-muted">{label}</span>
      <Avatar initials={initialsOf(name)} email={email} size={18} tone={avatarTone} />
      <span className={cn("font-medium", muted || unresolvedTitle ? "text-muted" : "text-ink")}>
        {name}
      </span>
      {unresolvedTitle && (
        <span
          aria-label={unresolvedTitle}
          title={unresolvedTitle}
          data-testid={`${testId}-unresolved`}
          className="text-[10px] text-escalation"
        >
          ⚠
        </span>
      )}
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
        className="inline-flex items-center gap-1.5 rounded-md px-1.5 py-0.5 text-xs opacity-70"
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
      className="inline-flex items-center gap-1.5 rounded-md px-1.5 py-0.5 text-xs transition-colors duration-100 hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
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
