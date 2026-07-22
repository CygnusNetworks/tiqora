import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, ApiError, type CustomerRef } from "@/lib/api";
import { useAuth } from "@/auth/AuthContext";
import { flattenQueues } from "@/components/agent/QueueTree";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { Badge } from "@/components/ui/Badge";
import { stateLabel } from "@/lib/status";
import { cn } from "@/lib/cn";
import {
  RecipientsField,
  joinRecipients,
  moveRecipientBetween,
  sameRecipient,
  type Recipient,
} from "@/components/agent/RecipientsField";
import { ComposerBody } from "@/components/agent/ComposerBody";
import { ArticleBodyRenderer } from "@/components/agent/ArticleBodyRenderer";

const FIELD_CLASS =
  "w-full rounded-md border border-hairline bg-surface-subtle px-3 py-2 text-[13.5px] text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent focus:border-accent";

const toggleCls =
  "inline-flex items-center gap-1 rounded border border-hairline px-2 py-0.5 text-muted transition-colors duration-100 hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";
const toggleActiveCls = "border-accent/50 bg-accent-dim text-accent hover:text-accent";
const countBadgeCls =
  "rounded-full bg-accent-dim px-1.5 py-0.5 font-mono text-[10px] tabular-nums text-accent";

function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const handle = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(handle);
  }, [value, delayMs]);
  return debounced;
}

/** Pick a sensible default option id: the first whose name/type matches one of
 * `prefer` (case-insensitive), else the first option. Keeps the form usable
 * without hard-coding backend ids that differ per install. */
function defaultId(
  options: { id: number; name: string; type_name?: string }[],
  prefer: string[],
): number | undefined {
  for (const want of prefer) {
    const hit = options.find(
      (o) =>
        o.name.toLowerCase().includes(want) || o.type_name?.toLowerCase().includes(want),
    );
    if (hit) return hit.id;
  }
  return options[0]?.id;
}

type TicketType = "email" | "phone";
type RecipientField = "to" | "cc" | "bcc";
type Direction = "inbound" | "outbound";

/**
 * Agent-facing New-ticket compose page, reached from the top bar's "＋ New".
 * Customer-first flow ("Variante 2"): pick or skip a customer before the rest
 * of the form unlocks. Two ticket types: an email ticket sends a real SMTP
 * message via the same agent-reply pipeline as `ReplyDialog` (compose-context
 * resolves From/signature/rich-text per queue), or a phone ticket that just
 * records a customer/agent note with no email dispatch. The queue may be
 * pre-selected via the `queue_id` search param set by the top-bar queue picker.
 */
export type NewTicketSearch = { queue_id?: number };

export function NewTicketPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { user } = useAuth();
  const { queue_id: queueId } = useSearch({ from: "/agent/tickets/new" }) as NewTicketSearch;

  const queuesQ = useQuery({ queryKey: ["queues"], queryFn: () => api.listQueues() });
  const prioritiesQ = useQuery({
    queryKey: ["reference", "priorities"],
    queryFn: () => api.listReferencePriorities(),
  });
  const statesQ = useQuery({
    queryKey: ["reference", "states"],
    queryFn: () => api.listReferenceStates(),
  });

  const queues = useMemo(
    () => flattenQueues(queuesQ.data ?? []).filter((q) => q.valid),
    [queuesQ.data],
  );
  const priorities = useMemo(() => prioritiesQ.data ?? [], [prioritiesQ.data]);
  // Only offer states an agent would set on a fresh ticket (new/open), not
  // closed/removed types.
  const states = useMemo(
    () =>
      (statesQ.data ?? []).filter(
        (s) => s.type_name === "new" || s.type_name === "open" || s.type_name === "pending auto",
      ),
    [statesQ.data],
  );

  const [ticketType, setTicketType] = useState<TicketType>("email");

  const [customer, setCustomer] = useState<CustomerRef | null>(null);
  const [customerQuery, setCustomerQuery] = useState("");
  const [skipCustomer, setSkipCustomer] = useState(false);
  const debouncedCustomerQuery = useDebouncedValue(customerQuery, 250);

  const [queue, setQueue] = useState<number | "">(queueId ?? "");
  const [subject, setSubject] = useState("");
  const [priority, setPriority] = useState<number | "">("");
  const [state, setState] = useState<number | "">("");
  const [body, setBody] = useState("");
  const [direction, setDirection] = useState<Direction>("inbound");

  const [to, setTo] = useState<Recipient[]>([]);
  const [cc, setCc] = useState<Recipient[]>([]);
  const [bcc, setBcc] = useState<Recipient[]>([]);
  const [showCc, setShowCc] = useState(false);
  const [showBcc, setShowBcc] = useState(false);

  const [error, setError] = useState<string | null>(null);
  const [createdTicketId, setCreatedTicketId] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Unlocked once a customer is chosen, or the agent explicitly opts to
  // continue without one — the escape hatch button stays outside this gate.
  const formUnlocked = customer !== null || skipCustomer;

  const customersQ = useQuery({
    queryKey: ["reference", "customer-search", debouncedCustomerQuery],
    queryFn: () => api.searchReferenceCustomers({ q: debouncedCustomerQuery }),
    enabled: !customer && debouncedCustomerQuery.trim().length >= 2,
  });

  const composeContextQ = useQuery({
    queryKey: ["reference", "compose-context", queue],
    queryFn: () => api.getComposeContext(queue as number),
    enabled: queue !== "",
  });

  // Seed the selects with sensible defaults once their options load, without
  // clobbering a value the user (or the queue picker) already chose.
  useEffect(() => {
    if (queue === "" && queues.length > 0) setQueue(queueId ?? queues[0].id);
  }, [queues, queueId, queue]);
  useEffect(() => {
    if (priority === "" && priorities.length > 0) {
      setPriority(defaultId(priorities, ["normal", "3"]) ?? priorities[0].id);
    }
  }, [priorities, priority]);
  useEffect(() => {
    if (state === "" && states.length > 0) {
      setState(defaultId(states, ["open", "new"]) ?? states[0].id);
    }
  }, [states, state]);

  const selectCustomer = (c: CustomerRef) => {
    setCustomer(c);
    setCustomerQuery("");
    if (ticketType === "email") {
      const seed: Recipient = { name: c.full_name, email: c.email };
      setTo((prev) => (prev.some((r) => sameRecipient(r, seed)) ? prev : [...prev, seed]));
    }
  };

  const clearCustomer = () => {
    setCustomer(null);
    setCustomerQuery("");
    // Manually-entered recipients stay — only the auto-seed link is removed.
  };

  const setters: Record<RecipientField, (r: Recipient[]) => void> = {
    to: setTo,
    cc: setCc,
    bcc: setBcc,
  };
  const values: Record<RecipientField, Recipient[]> = { to, cc, bcc };

  const moveRecipient = (from: string, dest: string, r: Recipient) => {
    if (from === dest) return;
    const fromKey = from as RecipientField;
    const destKey = dest as RecipientField;
    if (!(fromKey in values) || !(destKey in values)) return;
    const { source, target } = moveRecipientBetween(values[fromKey], values[destKey], r);
    setters[fromKey](source);
    setters[destKey](target);
    if (destKey === "cc") setShowCc(true);
    if (destKey === "bcc") setShowBcc(true);
  };

  const richText = composeContextQ.data?.rich_text ?? false;

  const canSubmit =
    formUnlocked &&
    queue !== "" &&
    priority !== "" &&
    state !== "" &&
    subject.trim().length > 0 &&
    body.trim().length > 0 &&
    (ticketType === "phone" || to.length > 0) &&
    !submitting;

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setCreatedTicketId(null);
    // `canSubmit` already asserts queue/priority/state are set (TS narrows
    // them to `number` below via aliased-condition control-flow analysis).
    if (!canSubmit || !user) {
      setError(t("newTicket.validationError"));
      return;
    }
    setSubmitting(true);
    try {
      const { ticket_id } = await api.createTicket({
        title: subject.trim(),
        queue_id: queue,
        state_id: state,
        priority_id: priority,
        owner_id: user.id,
        customer_user_id: customer?.login ?? null,
      });
      setCreatedTicketId(ticket_id);
      // The article can fail independently of ticket creation (e.g. an agent
      // email reply whose SMTP delivery fails, HTTP 502) — the ticket already
      // exists at that point, so surface an error with a link to it instead
      // of silently losing the agent's typed message.
      try {
        if (ticketType === "email") {
          await api.createArticle(ticket_id, {
            sender_type: "agent",
            channel: "email",
            is_visible_for_customer: true,
            subject: subject.trim(),
            body,
            content_type: richText ? "text/html; charset=utf-8" : "text/plain; charset=utf-8",
            to_address: joinRecipients(to),
            cc: joinRecipients(cc),
            bcc: joinRecipients(bcc),
          });
        } else {
          await api.createArticle(ticket_id, {
            sender_type: direction === "inbound" ? "customer" : "agent",
            channel: "phone",
            is_visible_for_customer: true,
            subject: subject.trim(),
            body,
            content_type: "text/plain; charset=utf-8",
            from_address: direction === "inbound" ? (customer?.email ?? null) : null,
            to_address: direction === "outbound" ? (customer?.email ?? null) : null,
          });
        }
      } catch (articleErr) {
        if (!(articleErr instanceof ApiError)) throw articleErr;
        setError(ticketType === "email" ? t("newTicket.sendError") : t("newTicket.submitError"));
        return;
      }
      await navigate({
        to: "/agent/tickets/$ticketId",
        params: { ticketId: String(ticket_id) },
      });
    } catch (err) {
      if (err instanceof ApiError) {
        setError(t("newTicket.submitError"));
      } else {
        throw err;
      }
    } finally {
      setSubmitting(false);
    }
  };

  const loading = queuesQ.isLoading || prioritiesQ.isLoading || statesQ.isLoading;

  const ccOn = showCc || cc.length > 0;
  const bccOn = showBcc || bcc.length > 0;

  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-6" data-testid="agent-new-ticket-page">
      <h1 className="font-display text-xl font-semibold text-ink">{t("newTicket.title")}</h1>
      <p className="mt-1 text-[13px] text-muted">{t("newTicket.intro")}</p>

      <div className="mt-5 inline-flex overflow-hidden rounded-md border border-hairline text-sm">
        <button
          type="button"
          data-testid="new-ticket-type-email"
          aria-pressed={ticketType === "email"}
          onClick={() => setTicketType("email")}
          className={cn(
            "px-3 py-1.5",
            ticketType === "email" ? "bg-accent text-accent-ink" : "bg-surface text-muted hover:text-ink",
          )}
        >
          {t("newTicket.typeEmail")}
        </button>
        <button
          type="button"
          data-testid="new-ticket-type-phone"
          aria-pressed={ticketType === "phone"}
          onClick={() => setTicketType("phone")}
          className={cn(
            "border-l border-hairline px-3 py-1.5",
            ticketType === "phone" ? "bg-accent text-accent-ink" : "bg-surface text-muted hover:text-ink",
          )}
        >
          {t("newTicket.typePhone")}
        </button>
      </div>

      {loading ? (
        <div className="mt-6 flex justify-center py-10">
          <Spinner />
        </div>
      ) : (
        <form onSubmit={(e) => void onSubmit(e)} className="mt-4 space-y-4">
          <div className="rounded-xl border border-hairline bg-surface p-4">
            {customer ? (
              <div
                className="flex items-center gap-2 rounded border border-hairline bg-surface-subtle px-3 py-2 text-sm"
                data-testid="new-ticket-customer-card"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium text-ink">{customer.full_name}</p>
                  <p className="truncate text-xs text-muted">
                    {customer.email} · {customer.login}
                  </p>
                </div>
                {customer.customer_id && <Badge tone="muted">{customer.customer_id}</Badge>}
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  data-testid="new-ticket-customer-clear"
                  onClick={clearCustomer}
                >
                  {t("newTicket.customerChange")}
                </Button>
              </div>
            ) : (
              <>
                <input
                  data-testid="new-ticket-customer-search"
                  value={customerQuery}
                  autoFocus
                  onChange={(e) => setCustomerQuery(e.target.value)}
                  placeholder={t("newTicket.customerSearch")}
                  className={FIELD_CLASS}
                />
                <p className="mt-1 text-[11px] text-muted">{t("newTicket.customerSearchHint")}</p>
                {debouncedCustomerQuery.trim().length >= 2 && (
                  <div className="mt-2 max-h-56 overflow-auto rounded border border-hairline">
                    {customersQ.isLoading ? (
                      <div className="flex justify-center py-3">
                        <Spinner />
                      </div>
                    ) : (customersQ.data ?? []).length === 0 ? (
                      <p className="px-3 py-2 text-xs text-muted">
                        {t("newTicket.noCustomerResults")}
                      </p>
                    ) : (
                      (customersQ.data ?? []).map((c) => (
                        <button
                          key={c.login}
                          type="button"
                          data-testid={`new-ticket-customer-result-${c.login}`}
                          onClick={() => selectCustomer(c)}
                          className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm text-ink hover:bg-surface-subtle"
                        >
                          <span className="min-w-0 flex-1 truncate">
                            <span className="font-medium">{c.full_name}</span>{" "}
                            <span className="text-muted">{c.email}</span>
                          </span>
                          {c.customer_id && (
                            <Badge tone="muted" className="ml-auto shrink-0">
                              {c.customer_id}
                            </Badge>
                          )}
                        </button>
                      ))
                    )}
                  </div>
                )}
                {!formUnlocked && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="mt-2"
                    data-testid="new-ticket-skip-customer"
                    onClick={() => setSkipCustomer(true)}
                  >
                    {t("newTicket.skipCustomer")}
                  </Button>
                )}
              </>
            )}
          </div>

          <fieldset
            disabled={!formUnlocked}
            className={cn(
              "space-y-4 rounded-xl border border-hairline bg-surface p-5",
              !formUnlocked && "pointer-events-none opacity-50",
            )}
          >
            <div className="grid gap-4 sm:grid-cols-2">
              <label className="block">
                <span className="mb-1 block text-[12px] font-medium text-muted">
                  {t("newTicket.queue")}
                </span>
                <select
                  data-testid="new-ticket-queue"
                  required
                  value={queue}
                  onChange={(e) => setQueue(e.target.value ? Number(e.target.value) : "")}
                  className={FIELD_CLASS}
                >
                  {queues.length === 0 && <option value="">{t("newTicket.noQueues")}</option>}
                  {queues.map((q) => (
                    <option key={q.id} value={q.id}>
                      {q.name}
                    </option>
                  ))}
                </select>
              </label>

              {ticketType === "email" ? (
                <div className="block">
                  <span className="mb-1 block text-[12px] font-medium text-muted">
                    {t("newTicket.from")}
                  </span>
                  <p
                    className="rounded-md border border-hairline bg-surface-subtle px-3 py-2 text-[13.5px] text-muted"
                    data-testid="new-ticket-from"
                  >
                    {composeContextQ.isLoading ? "…" : composeContextQ.data?.from_address ?? "—"}
                  </p>
                </div>
              ) : (
                <div className="block">
                  <span className="mb-1 block text-[12px] font-medium text-muted">
                    {t("newTicket.directionLabel")}
                  </span>
                  <div className="flex gap-1.5">
                    <button
                      type="button"
                      data-testid="new-ticket-direction-in"
                      aria-pressed={direction === "inbound"}
                      onClick={() => setDirection("inbound")}
                      className={cn(toggleCls, direction === "inbound" && toggleActiveCls)}
                    >
                      {t("newTicket.directionIn")}
                    </button>
                    <button
                      type="button"
                      data-testid="new-ticket-direction-out"
                      aria-pressed={direction === "outbound"}
                      onClick={() => setDirection("outbound")}
                      className={cn(toggleCls, direction === "outbound" && toggleActiveCls)}
                    >
                      {t("newTicket.directionOut")}
                    </button>
                  </div>
                </div>
              )}
            </div>

            {ticketType === "email" && (
              <div className="space-y-2">
                <RecipientsField
                  label={t("ticket.replyTo")}
                  fieldKey="to"
                  recipients={to}
                  onChange={setTo}
                  onMove={moveRecipient}
                  required
                  placeholder={t("ticket.recipientAddHint")}
                  testid="new-ticket-to"
                />
                {showCc && (
                  <RecipientsField
                    label={t("ticket.replyCc")}
                    fieldKey="cc"
                    recipients={cc}
                    onChange={setCc}
                    onMove={moveRecipient}
                    placeholder={t("ticket.recipientAddHint")}
                    testid="new-ticket-cc"
                  />
                )}
                {showBcc && (
                  <RecipientsField
                    label={t("ticket.replyBcc")}
                    fieldKey="bcc"
                    recipients={bcc}
                    onChange={setBcc}
                    onMove={moveRecipient}
                    placeholder={t("ticket.recipientAddHint")}
                    testid="new-ticket-bcc"
                  />
                )}
                <div className="flex flex-wrap items-center gap-1.5 text-xs">
                  <button
                    type="button"
                    className={cn(toggleCls, ccOn && toggleActiveCls)}
                    data-testid="new-ticket-toggle-cc"
                    data-active={ccOn ? "true" : "false"}
                    aria-expanded={showCc}
                    onClick={() => setShowCc((v) => !v)}
                  >
                    {t("ticket.replyCc")}
                    {!showCc && cc.length > 0 && (
                      <span className={countBadgeCls}>{cc.length}</span>
                    )}
                  </button>
                  <button
                    type="button"
                    className={cn(toggleCls, bccOn && toggleActiveCls)}
                    data-testid="new-ticket-toggle-bcc"
                    data-active={bccOn ? "true" : "false"}
                    aria-expanded={showBcc}
                    onClick={() => setShowBcc((v) => !v)}
                  >
                    {t("ticket.replyBcc")}
                    {!showBcc && bcc.length > 0 && (
                      <span className={countBadgeCls}>{bcc.length}</span>
                    )}
                  </button>
                </div>
              </div>
            )}

            <label className="block">
              <span className="mb-1 block text-[12px] font-medium text-muted">
                {t("newTicket.subject")}
              </span>
              <input
                data-testid="new-ticket-subject"
                required
                value={subject}
                onChange={(e) => setSubject(e.target.value)}
                className={FIELD_CLASS}
              />
            </label>

            <div className="grid gap-4 sm:grid-cols-2">
              <label className="block">
                <span className="mb-1 block text-[12px] font-medium text-muted">
                  {t("newTicket.priority")}
                </span>
                <select
                  data-testid="new-ticket-priority"
                  required
                  value={priority}
                  onChange={(e) => setPriority(e.target.value ? Number(e.target.value) : "")}
                  className={FIELD_CLASS}
                >
                  {priorities.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </label>

              <label className="block">
                <span className="mb-1 block text-[12px] font-medium text-muted">
                  {t("newTicket.state")}
                </span>
                <select
                  data-testid="new-ticket-state"
                  required
                  value={state}
                  onChange={(e) => setState(e.target.value ? Number(e.target.value) : "")}
                  className={FIELD_CLASS}
                >
                  {states.map((s) => (
                    <option key={s.id} value={s.id}>
                      {stateLabel(t, s.name)}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <label className="block">
              <span className="mb-1 block text-[12px] font-medium text-muted">
                {ticketType === "email" ? t("newTicket.message") : t("newTicket.note")}
              </span>
              <ComposerBody
                richText={ticketType === "email" && richText}
                value={body}
                onChange={setBody}
                testId="new-ticket-body"
              />
            </label>

            {ticketType === "email" && Boolean(composeContextQ.data?.signature?.trim()) && (
              <div
                className="rounded border border-hairline bg-surface-subtle/60 p-2"
                data-testid="new-ticket-signature-preview"
              >
                <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted">
                  {t("ticket.signaturePreview")}
                </p>
                {composeContextQ.data?.signature_is_html ? (
                  <ArticleBodyRenderer
                    body={composeContextQ.data.signature}
                    isHtml
                    className="text-xs"
                  />
                ) : (
                  <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words font-mono text-xs text-ink">
                    {composeContextQ.data?.signature}
                  </pre>
                )}
              </div>
            )}
          </fieldset>

          {error && (
            <p className="text-[13px] text-danger" data-testid="new-ticket-error" role="alert">
              {error}
              {createdTicketId && (
                <>
                  {" "}
                  <Link
                    to="/agent/tickets/$ticketId"
                    params={{ ticketId: String(createdTicketId) }}
                    className="underline"
                  >
                    {t("newTicket.goToTicket")}
                  </Link>
                </>
              )}
            </p>
          )}

          <div className="flex items-center justify-end gap-2">
            <Button
              type="button"
              variant="ghost"
              onClick={() => void navigate({ to: "/agent" })}
              data-testid="new-ticket-cancel"
            >
              {t("newTicket.cancel")}
            </Button>
            <Button
              type="submit"
              variant="primary"
              disabled={!canSubmit}
              data-testid="new-ticket-submit"
            >
              {submitting ? (
                <Spinner />
              ) : ticketType === "email" ? (
                t("newTicket.submitSend")
              ) : (
                t("newTicket.submit")
              )}
            </Button>
          </div>
        </form>
      )}
    </div>
  );
}
