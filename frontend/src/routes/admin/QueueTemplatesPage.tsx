import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { Spinner } from "@/components/ui/Spinner";
import { Badge } from "@/components/ui/Badge";

/**
 * Queue↔Templates assignment editor: pick a queue, multi-select templates.
 *
 * Backend exposes assign (PUT) and revoke (DELETE) only — there is no GET for
 * the current set. Checkboxes are session-local optimistic state: checking
 * calls assign, unchecking calls revoke. Switching queues resets the view.
 */
export function QueueTemplatesPage() {
  const { t } = useTranslation();
  const [queueId, setQueueId] = useState<number | null>(null);
  const [assignedIds, setAssignedIds] = useState<Set<number>>(new Set());

  const queuesQ = useQuery({
    queryKey: ["admin", "queues", "ref"],
    queryFn: () => api.adminQueues.list({ valid: "valid", pageSize: 500 }),
    staleTime: 5 * 60 * 1000,
  });

  const templatesQ = useQuery({
    queryKey: ["admin", "templates", "ref"],
    queryFn: () => api.adminTemplates.list({ valid: "valid", pageSize: 500 }),
    staleTime: 5 * 60 * 1000,
  });

  useEffect(() => {
    setAssignedIds(new Set());
  }, [queueId]);

  const toggleM = useMutation({
    mutationFn: ({ templateId, next }: { templateId: number; next: boolean }) =>
      next
        ? api.assignQueueTemplate(queueId as number, templateId)
        : api.revokeQueueTemplate(queueId as number, templateId),
    onSuccess: (_data, { templateId, next }) => {
      setAssignedIds((prev) => {
        const n = new Set(prev);
        if (next) n.add(templateId);
        else n.delete(templateId);
        return n;
      });
    },
  });

  const templates = templatesQ.data?.items ?? [];

  return (
    <div className="space-y-4 p-4" data-testid="admin-queue-templates-page">
      <div>
        <h1 className="font-display text-xl font-semibold text-ink">
          {t("admin.queueTemplates.title")}
        </h1>
        <p className="mt-1 text-sm text-muted">{t("admin.queueTemplates.subtitle")}</p>
      </div>

      <label className="block max-w-md space-y-1">
        <span className="text-xs font-medium uppercase tracking-wide text-muted">
          {t("admin.queueTemplates.queue")}
        </span>
        <select
          className="w-full rounded-lg border border-hairline bg-surface px-3 py-2 text-sm text-ink"
          data-testid="admin-queue-templates-select"
          value={queueId ?? ""}
          onChange={(e) => {
            const v = e.currentTarget.value;
            setQueueId(v ? Number(v) : null);
          }}
        >
          <option value="">{t("admin.queueTemplates.selectQueue")}</option>
          {(queuesQ.data?.items ?? []).map((q) => (
            <option key={q.id} value={q.id}>
              {q.name}
            </option>
          ))}
        </select>
      </label>

      {queueId === null ? (
        <p className="rounded-lg border border-dashed border-hairline bg-surface px-4 py-8 text-center text-sm text-muted">
          {t("admin.queueTemplates.empty")}
        </p>
      ) : (
        <div className="rounded-lg border border-hairline bg-surface">
          <div className="flex items-center justify-between border-b border-hairline px-4 py-2 text-xs uppercase tracking-wide text-muted">
            <span>{t("admin.queueTemplates.templates")}</span>
            {toggleM.isPending && <Spinner className="size-4" />}
          </div>
          <ul className="divide-y divide-hairline">
            {templates.map((tmpl) => {
              const checked = assignedIds.has(tmpl.id);
              return (
                <li
                  key={tmpl.id}
                  className="flex items-center justify-between px-4 py-2.5"
                  data-testid={`admin-queue-template-row-${tmpl.id}`}
                >
                  <label className="flex items-center gap-3">
                    <input
                      type="checkbox"
                      className="size-4 accent-accent"
                      checked={checked}
                      disabled={toggleM.isPending}
                      data-testid={`admin-queue-template-toggle-${tmpl.id}`}
                      onChange={(e) =>
                        toggleM.mutate({ templateId: tmpl.id, next: e.target.checked })
                      }
                    />
                    <span className="text-sm text-ink">
                      {tmpl.name}
                      <span className="ml-2 text-xs text-muted">{tmpl.template_type}</span>
                    </span>
                  </label>
                  {checked && (
                    <Badge tone="success">{t("admin.queueTemplates.assigned")}</Badge>
                  )}
                </li>
              );
            })}
            {templates.length === 0 && (
              <li className="px-4 py-6 text-center text-sm text-muted">
                {t("admin.queueTemplates.noTemplates")}
              </li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
}
