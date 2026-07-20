import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/auth/AuthContext";
import { flattenQueues } from "@/components/agent/QueueTree";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { stateLabel } from "@/lib/status";

const FIELD_CLASS =
  "w-full rounded-md border border-hairline bg-surface-subtle px-3 py-2 text-[13.5px] text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent focus:border-accent";

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

/**
 * Agent-facing quick New-ticket form, reached from the top bar's "＋ New".
 * Two-step create: POST the ticket (queue/state/priority/owner/customer) then
 * add the typed message as the first article. The queue may be pre-selected
 * via the `queue_id` search param set by the top-bar queue picker.
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

  const [queue, setQueue] = useState<number | "">(queueId ?? "");
  const [subject, setSubject] = useState("");
  const [customer, setCustomer] = useState("");
  const [priority, setPriority] = useState<number | "">("");
  const [state, setState] = useState<number | "">("");
  const [body, setBody] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

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

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (queue === "" || priority === "" || state === "" || !subject.trim() || !body.trim()) {
      setError(t("newTicket.validationError"));
      return;
    }
    if (!user) return;
    setSubmitting(true);
    try {
      const { ticket_id } = await api.createTicket({
        title: subject.trim(),
        queue_id: queue,
        state_id: state,
        priority_id: priority,
        owner_id: user.id,
        customer_user_id: customer.trim() || null,
      });
      // The typed message becomes the ticket's first article. A failure here
      // is surfaced but the ticket already exists, so we still route to it.
      try {
        await api.createArticle(ticket_id, {
          sender_type: "agent",
          is_visible_for_customer: true,
          subject: subject.trim(),
          body: body.trim(),
          content_type: "text/plain; charset=utf-8",
          channel: "note",
        });
      } catch (articleErr) {
        if (!(articleErr instanceof ApiError)) throw articleErr;
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

  return (
    <div className="mx-auto w-full max-w-2xl px-4 py-6" data-testid="agent-new-ticket-page">
      <h1 className="font-display text-xl font-semibold text-ink">{t("newTicket.title")}</h1>
      <p className="mt-1 text-[13px] text-muted">{t("newTicket.intro")}</p>

      {loading ? (
        <div className="mt-6 flex justify-center py-10">
          <Spinner />
        </div>
      ) : (
        <form
          onSubmit={(e) => void onSubmit(e)}
          className="mt-5 space-y-4 rounded-xl border border-hairline bg-surface p-5"
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

            <label className="block">
              <span className="mb-1 block text-[12px] font-medium text-muted">
                {t("newTicket.customer")}
              </span>
              <input
                data-testid="new-ticket-customer"
                value={customer}
                onChange={(e) => setCustomer(e.target.value)}
                placeholder={t("newTicket.customerHint")}
                className={FIELD_CLASS}
              />
            </label>
          </div>

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
              {t("newTicket.message")}
            </span>
            <textarea
              data-testid="new-ticket-body"
              required
              rows={7}
              value={body}
              onChange={(e) => setBody(e.target.value)}
              className={`${FIELD_CLASS} resize-y`}
            />
          </label>

          {error && (
            <p className="text-[13px] text-danger" data-testid="new-ticket-error" role="alert">
              {error}
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
              disabled={submitting}
              data-testid="new-ticket-submit"
            >
              {submitting ? <Spinner /> : t("newTicket.submit")}
            </Button>
          </div>
        </form>
      )}
    </div>
  );
}
