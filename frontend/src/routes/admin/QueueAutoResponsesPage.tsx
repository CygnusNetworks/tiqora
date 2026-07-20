import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { Spinner } from "@/components/ui/Spinner";
import { Badge } from "@/components/ui/Badge";

/**
 * Queue↔Auto-responses assignment editor: pick a queue, multi-select
 * auto-responses. Backend is assign (PUT one id) / revoke (DELETE) only —
 * no GET for the current set — so checkbox state is optimistic/session-local
 * (same pattern as Queue↔Templates).
 */
export function QueueAutoResponsesPage() {
  const { t } = useTranslation();
  const [queueId, setQueueId] = useState<number | null>(null);
  const [assignedIds, setAssignedIds] = useState<Set<number>>(new Set());

  const queuesQ = useQuery({
    queryKey: ["admin", "queues", "ref"],
    queryFn: () => api.adminQueues.list({ valid: "valid", pageSize: 500 }),
    staleTime: 5 * 60 * 1000,
  });

  const autoResponsesQ = useQuery({
    queryKey: ["admin", "auto-responses", "ref"],
    queryFn: () => api.adminAutoResponses.list({ valid: "valid", pageSize: 500 }),
    staleTime: 5 * 60 * 1000,
  });

  useEffect(() => {
    setAssignedIds(new Set());
  }, [queueId]);

  const toggleM = useMutation({
    mutationFn: ({ autoResponseId, next }: { autoResponseId: number; next: boolean }) =>
      next
        ? api.assignQueueAutoResponse(queueId as number, autoResponseId)
        : api.revokeQueueAutoResponse(queueId as number, autoResponseId),
    onSuccess: (_data, { autoResponseId, next }) => {
      setAssignedIds((prev) => {
        const n = new Set(prev);
        if (next) n.add(autoResponseId);
        else n.delete(autoResponseId);
        return n;
      });
    },
  });

  const autoResponses = autoResponsesQ.data?.items ?? [];

  return (
    <div className="space-y-4 p-4" data-testid="admin-queue-auto-responses-page">
      <div>
        <h1 className="font-display text-xl font-semibold text-ink">
          {t("admin.queueAutoResponses.title")}
        </h1>
        <p className="mt-1 text-sm text-muted">{t("admin.queueAutoResponses.subtitle")}</p>
      </div>

      <label className="block max-w-md space-y-1">
        <span className="text-xs font-medium uppercase tracking-wide text-muted">
          {t("admin.queueAutoResponses.queue")}
        </span>
        <select
          className="w-full rounded-lg border border-hairline bg-surface px-3 py-2 text-sm text-ink"
          data-testid="admin-queue-auto-responses-select"
          value={queueId ?? ""}
          onChange={(e) => {
            const v = e.currentTarget.value;
            setQueueId(v ? Number(v) : null);
          }}
        >
          <option value="">{t("admin.queueAutoResponses.selectQueue")}</option>
          {(queuesQ.data?.items ?? []).map((q) => (
            <option key={q.id} value={q.id}>
              {q.name}
            </option>
          ))}
        </select>
      </label>

      {queueId === null ? (
        <p className="rounded-lg border border-dashed border-hairline bg-surface px-4 py-8 text-center text-sm text-muted">
          {t("admin.queueAutoResponses.empty")}
        </p>
      ) : (
        <div className="rounded-lg border border-hairline bg-surface">
          <div className="flex items-center justify-between border-b border-hairline px-4 py-2 text-xs uppercase tracking-wide text-muted">
            <span>{t("admin.queueAutoResponses.autoResponses")}</span>
            {toggleM.isPending && <Spinner className="size-4" />}
          </div>
          <ul className="divide-y divide-hairline">
            {autoResponses.map((ar) => {
              const checked = assignedIds.has(ar.id);
              return (
                <li
                  key={ar.id}
                  className="flex items-center justify-between px-4 py-2.5"
                  data-testid={`admin-queue-auto-response-row-${ar.id}`}
                >
                  <label className="flex items-center gap-3">
                    <input
                      type="checkbox"
                      className="size-4 accent-accent"
                      checked={checked}
                      disabled={toggleM.isPending}
                      data-testid={`admin-queue-auto-response-toggle-${ar.id}`}
                      onChange={(e) =>
                        toggleM.mutate({
                          autoResponseId: ar.id,
                          next: e.target.checked,
                        })
                      }
                    />
                    <span className="text-sm text-ink">
                      {ar.name}
                      <span className="ml-2 text-xs text-muted">
                        {t("admin.queueAutoResponses.typeId")}: {ar.type_id}
                      </span>
                    </span>
                  </label>
                  {checked && (
                    <Badge tone="success">{t("admin.queueAutoResponses.assigned")}</Badge>
                  )}
                </li>
              );
            })}
            {autoResponses.length === 0 && (
              <li className="px-4 py-6 text-center text-sm text-muted">
                {t("admin.queueAutoResponses.noAutoResponses")}
              </li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
}
