import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";

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
          disabled={!to.trim() || m.isPending}
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
        <label className="block text-xs text-muted">
          {t("ticket.replyTo")}
          <input className={inputCls} value={to} onChange={(e) => setTo(e.target.value)} />
        </label>
        {m.isError && <p className="text-xs text-danger">{t("ticket.dialog.genericError")}</p>}
        <DialogActions
          onCancel={onClose}
          onSave={() => m.mutate()}
          disabled={!to.trim() || m.isPending}
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

  const queuesQ = useQuery({
    queryKey: ["queues"],
    queryFn: () => api.listQueues(),
    enabled: open,
  });
  const queues = flattenQueues(queuesQ.data ?? []);

  const m = useMutation({
    mutationFn: () =>
      api.splitArticle(ticketId, articleId, {
        queue_id: Number(queueId),
        title: title || null,
      }),
    onSuccess: () => onClose(),
  });

  return (
    <Dialog open={open} onClose={onClose} title={t("ticket.splitDialogTitle")}>
      <div className="space-y-2" data-testid="split-dialog">
        <label className="block text-xs text-muted">
          {t("ticket.splitQueue")}
          <select
            className={inputCls}
            value={queueId}
            onChange={(e) => setQueueId(e.target.value)}
          >
            <option value="">{t("ticket.dialog.selectPlaceholder")}</option>
            {queues.map((q) => (
              <option key={q.id} value={q.id}>
                {q.name}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-xs text-muted">
          {t("ticket.splitNewTitle")}
          <input className={inputCls} value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
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
