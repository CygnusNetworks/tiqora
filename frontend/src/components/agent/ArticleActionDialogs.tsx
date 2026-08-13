import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, type CustomerRef } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { SelectField } from "@/components/ui/SelectField";
import { useComposerLock } from "@/lib/composerLock";
import { ComposerLockBanner } from "./ComposerLock";

const inputCls =
  "w-full rounded border border-hairline bg-surface px-2 py-1.5 text-sm text-ink placeholder:text-muted focus:outline-none focus:ring-1 focus:ring-accent";

function useInvalidateTicket(ticketId: number) {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: ["tickets", ticketId, "articles"] });
    void qc.invalidateQueries({ queryKey: ["tickets", ticketId] });
  };
}

export function ForwardDialog({
  ticketId,
  articleId,
  open,
  onClose,
}: {
  ticketId: number;
  articleId: number;
  open: boolean;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const invalidate = useInvalidateTicket(ticketId);
  const ticketLock = useComposerLock(ticketId, "forward", open);
  const [to, setTo] = useState("");
  const [note, setNote] = useState("");

  const m = useMutation({
    mutationFn: () =>
      api.forwardArticle(ticketId, articleId, {
        to_address: to,
        note: note || null,
        body: "",
      }),
    onSuccess: () => {
      invalidate();
      onClose();
    },
  });

  return (
    <Dialog open={open} onClose={onClose} title={t("ticket.forwardDialogTitle")}>
      <div className="space-y-2" data-testid="forward-dialog">
        <ComposerLockBanner
          lockedBy={ticketLock.lockedBy}
          onTakeOver={ticketLock.takeOver}
          busy={ticketLock.takingOver}
        />
        <label className="block text-xs text-muted">
          {t("ticket.replyTo")}
          <input className={inputCls} value={to} onChange={(e) => setTo(e.target.value)} />
        </label>
        <label className="block text-xs text-muted">
          {t("ticket.forwardNote")}
          <textarea
            className={inputCls}
            rows={4}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </label>
        {m.isError && <p className="text-xs text-danger">{t("ticket.dialog.genericError")}</p>}
        <DialogActions
          onCancel={onClose}
          onSave={() => m.mutate()}
          disabled={!to.trim() || m.isPending || ticketLock.lockedBy !== null}
        />
      </div>
    </Dialog>
  );
}

export function BounceDialog({
  ticketId,
  articleId,
  open,
  onClose,
}: {
  ticketId: number;
  articleId: number;
  open: boolean;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const invalidate = useInvalidateTicket(ticketId);
  const ticketLock = useComposerLock(ticketId, "bounce", open);
  const [to, setTo] = useState("");

  const m = useMutation({
    mutationFn: () => api.bounceArticle(ticketId, articleId, { to_address: to }),
    onSuccess: () => {
      invalidate();
      onClose();
    },
  });

  return (
    <Dialog open={open} onClose={onClose} title={t("ticket.bounceDialogTitle")}>
      <div className="space-y-2" data-testid="bounce-dialog">
        <ComposerLockBanner
          lockedBy={ticketLock.lockedBy}
          onTakeOver={ticketLock.takeOver}
          busy={ticketLock.takingOver}
        />
        <label className="block text-xs text-muted">
          {t("ticket.replyTo")}
          <input className={inputCls} value={to} onChange={(e) => setTo(e.target.value)} />
        </label>
        {m.isError && <p className="text-xs text-danger">{t("ticket.dialog.genericError")}</p>}
        <DialogActions
          onCancel={onClose}
          onSave={() => m.mutate()}
          disabled={!to.trim() || m.isPending || ticketLock.lockedBy !== null}
        />
      </div>
    </Dialog>
  );
}

export function SplitDialog({
  ticketId,
  articleId,
  open,
  onClose,
}: {
  ticketId: number;
  articleId: number;
  open: boolean;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [queueId, setQueueId] = useState("");
  const [title, setTitle] = useState("");
  const [priorityId, setPriorityId] = useState("");
  const [stateId, setStateId] = useState("");
  // Customer override for the new ticket: null = inherit the source ticket's.
  const [customer, setCustomer] = useState<CustomerRef | null>(null);
  const [changingCustomer, setChangingCustomer] = useState(false);
  const [customerQuery, setCustomerQuery] = useState("");
  const seededRef = useRef(false);

  const queuesQ = useQuery({
    queryKey: ["queues"],
    queryFn: () => api.listQueues(),
    enabled: open,
  });
  const queues = flattenQueues(queuesQ.data ?? []);

  // Source ticket — used to pre-fill queue/title/priority/state/customer.
  const ticketQ = useQuery({
    queryKey: ["tickets", ticketId],
    queryFn: () => api.getTicket(ticketId),
    enabled: open,
  });
  const prioritiesQ = useQuery({
    queryKey: ["reference", "priorities"],
    queryFn: () => api.listReferencePriorities(),
    enabled: open,
  });
  const statesQ = useQuery({
    queryKey: ["reference", "states"],
    queryFn: () => api.listReferenceStates(),
    enabled: open,
  });
  const customersQ = useQuery({
    queryKey: ["reference", "customers", customerQuery],
    queryFn: () => api.searchReferenceCustomers({ q: customerQuery }),
    enabled: open && changingCustomer && customerQuery.trim().length >= 2,
  });

  // Seed the form from the source ticket the first time it loads while open.
  useEffect(() => {
    const src = ticketQ.data;
    if (!open || !src || seededRef.current) return;
    seededRef.current = true;
    setQueueId(String(src.queue_id));
    setTitle(src.title ?? "");
    setPriorityId(String(src.priority_id));
    setStateId(String(src.state_id));
  }, [open, ticketQ.data]);

  // Reset seed + local edits when the dialog closes.
  useEffect(() => {
    if (open) return;
    seededRef.current = false;
    setCustomer(null);
    setChangingCustomer(false);
    setCustomerQuery("");
  }, [open]);

  const src = ticketQ.data;
  const currentCustomerLabel = customer
    ? `${customer.full_name} (${customer.login})`
    : (src?.customer_user_id ?? src?.customer_id ?? t("ticket.split.customerInherit"));

  const m = useMutation({
    mutationFn: () =>
      api.splitArticle(ticketId, articleId, {
        queue_id: Number(queueId),
        title: title || null,
        priority_id: priorityId ? Number(priorityId) : null,
        state_id: stateId ? Number(stateId) : null,
        customer_id: customer?.customer_id ?? null,
        customer_user_id: customer?.login ?? null,
      }),
    onSuccess: () => onClose(),
  });

  return (
    <Dialog open={open} onClose={onClose} title={t("ticket.splitDialogTitle")}>
      <div className="space-y-2.5" data-testid="split-dialog">
        <label className="block text-xs text-muted">
          {t("ticket.splitQueue")}
          <SelectField
            items={queues.map((q) => ({ value: String(q.id), label: q.name }))}
            value={queueId || null}
            onChange={setQueueId}
            placeholder={t("ticket.dialog.selectPlaceholder")}
            testId="split-queue-select"
          />
        </label>
        <label className="block text-xs text-muted">
          {t("ticket.splitNewTitle")}
          <input
            className={inputCls}
            data-testid="split-title-input"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </label>
        <div className="grid grid-cols-2 gap-2">
          <label className="block text-xs text-muted">
            {t("ticket.split.priority")}
            <SelectField
              items={(prioritiesQ.data ?? []).map((p) => ({
                value: String(p.id),
                label: p.name,
              }))}
              value={priorityId || null}
              onChange={setPriorityId}
              placeholder={t("ticket.dialog.selectPlaceholder")}
              testId="split-priority-select"
            />
          </label>
          <label className="block text-xs text-muted">
            {t("ticket.split.state")}
            <SelectField
              items={(statesQ.data ?? []).map((s) => ({
                value: String(s.id),
                label: s.name,
              }))}
              value={stateId || null}
              onChange={setStateId}
              placeholder={t("ticket.dialog.selectPlaceholder")}
              testId="split-state-select"
            />
          </label>
        </div>
        <div className="text-xs text-muted">
          <span className="mb-1 flex items-center justify-between">
            {t("ticket.split.customer")}
            <button
              type="button"
              className="text-accent hover:underline"
              data-testid="split-customer-toggle"
              onClick={() => setChangingCustomer((v) => !v)}
            >
              {changingCustomer ? t("common.cancel") : t("ticket.split.changeCustomer")}
            </button>
          </span>
          {!changingCustomer ? (
            <p className="truncate text-sm text-ink" data-testid="split-customer-current">
              {currentCustomerLabel}
            </p>
          ) : (
            <div className="space-y-1">
              <input
                className={inputCls}
                data-testid="split-customer-search"
                value={customerQuery}
                onChange={(e) => setCustomerQuery(e.target.value)}
                placeholder={t("ticket.split.customerSearch")}
              />
              {(customersQ.data ?? []).length > 0 && (
                <ul className="max-h-40 overflow-auto rounded border border-hairline">
                  {(customersQ.data ?? []).map((c) => (
                    <li key={c.login}>
                      <button
                        type="button"
                        data-testid={`split-customer-result-${c.login}`}
                        className="flex w-full flex-col px-2 py-1 text-left hover:bg-surface-subtle"
                        onClick={() => {
                          setCustomer(c);
                          setChangingCustomer(false);
                        }}
                      >
                        <span className="text-sm text-ink">{c.full_name}</span>
                        <span className="text-xs text-muted">
                          {c.login} · {c.email}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
        {m.isError && <p className="text-xs text-danger">{t("ticket.dialog.genericError")}</p>}
        <DialogActions
          onCancel={onClose}
          onSave={() => m.mutate()}
          disabled={!queueId || m.isPending}
        />
      </div>
    </Dialog>
  );
}

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
    <div className="flex items-center justify-end gap-1.5 pt-1">
      <Button variant="ghost" size="sm" onClick={onCancel}>
        {t("ticket.dialog.cancel")}
      </Button>
      <Button variant="primary" size="sm" disabled={disabled} onClick={onSave}>
        {t("ticket.dialog.save")}
      </Button>
    </div>
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
