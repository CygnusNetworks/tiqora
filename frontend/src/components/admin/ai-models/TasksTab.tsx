import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { aiApi, type AiTaskProfileItem, type LlmProfileOut } from "@/lib/aiApi";
import {
  AI_TASKS,
  AI_TASK_NEEDS,
  aiNeedLabelKey,
  aiTaskDescriptionKey,
  aiTaskFallbackKey,
  aiTaskNameKey,
  missingNeeds,
  taskProfileMap,
  type AiTask,
} from "@/lib/aiTasks";
import { Badge } from "@/components/ui/Badge";
import { SelectField } from "@/components/ui/SelectField";
import type { SelectMenuItem } from "@/components/ui/SelectMenu";
import { Spinner } from "@/components/ui/Spinner";
import { PROFILES_KEY, TASK_DEFAULTS_KEY, errorText, invalidateCatalog } from "./shared";

const NONE = "none";

export function TasksTab() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [status, setStatus] = useState<{ ok: boolean; text: string } | null>(null);

  const defaultsQ = useQuery({
    queryKey: TASK_DEFAULTS_KEY,
    queryFn: ({ signal }) => aiApi.getTaskDefaults(signal),
  });
  const profilesQ = useQuery({
    queryKey: PROFILES_KEY,
    queryFn: ({ signal }) => aiApi.listProfiles(signal),
  });
  const profiles = profilesQ.data ?? [];
  const current = taskProfileMap(defaultsQ.data);

  const saveM = useMutation({
    mutationFn: (body: AiTaskProfileItem[]) => aiApi.putTaskDefaults(body),
    onMutate: () => setStatus(null),
    onSuccess: async (data) => {
      qc.setQueryData(TASK_DEFAULTS_KEY, data);
      setStatus({ ok: true, text: t("admin.ai.tasks.saved") });
      await invalidateCatalog(qc);
    },
    onError: (err) =>
      setStatus({ ok: false, text: errorText(err, t("admin.form.genericError")) }),
  });

  const assign = (task: AiTask, value: string) => {
    const profileId = value === NONE ? null : Number(value);
    saveM.mutate(
      AI_TASKS.map((tk) => ({
        task: tk,
        profile_id: tk === task ? profileId : (current.get(tk) ?? null),
      })),
    );
  };

  const optionsFor = (task: AiTask): SelectMenuItem<string>[] => [
    {
      value: NONE,
      label: t("admin.ai.tasks.noProfile", { fallback: t(aiTaskFallbackKey(task)) }),
    },
    ...profiles.map((p: LlmProfileOut) => {
      const missing = missingNeeds(task, p.entries);
      return {
        value: String(p.id),
        label: p.name,
        disabled: missing.length > 0,
        hint:
          missing.length > 0
            ? t("admin.ai.tasks.cannot", {
                needs: missing.map((n) => t(aiNeedLabelKey(n))).join(", "),
              })
            : p.valid_id !== 1
              ? t("admin.ai.tasks.profileInactive")
              : undefined,
      };
    }),
  ];

  if (defaultsQ.isLoading || profilesQ.isLoading) {
    return (
      <div className="flex justify-center p-6">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="space-y-3" data-testid="admin-ai-tasks-tab">
      <p className="max-w-3xl text-xs text-muted">{t("admin.ai.tasks.intro")}</p>
      <div className="overflow-x-auto rounded-xl border border-hairline bg-surface">
        <table className="w-full text-sm" data-testid="admin-ai-tasks-table">
          <thead>
            <tr className="border-b border-hairline bg-surface-subtle text-left text-[11px] uppercase tracking-wide text-muted">
              <th className="px-3.5 py-2 font-semibold">{t("admin.ai.tasks.colTask")}</th>
              <th className="px-3.5 py-2 font-semibold">{t("admin.ai.tasks.colNeeds")}</th>
              <th className="px-3.5 py-2 font-semibold">{t("admin.ai.tasks.colProfile")}</th>
            </tr>
          </thead>
          <tbody>
            {AI_TASKS.map((task) => {
              const profileId = current.get(task) ?? null;
              const selected = profiles.find((p) => p.id === profileId);
              return (
                <tr
                  key={task}
                  data-testid={`admin-ai-task-row-${task}`}
                  className="border-t border-hairline first:border-t-0"
                >
                  <td className="px-3.5 py-2.5">
                    <div className="font-medium text-ink">{t(aiTaskNameKey(task))}</div>
                    <div className="text-xs text-muted">{t(aiTaskDescriptionKey(task))}</div>
                  </td>
                  <td className="px-3.5 py-2.5">
                    {AI_TASK_NEEDS[task].length === 0 ? (
                      <span className="text-xs text-muted">{t("admin.ai.tasks.needsNothing")}</span>
                    ) : (
                      <div className="flex flex-wrap gap-1">
                        {AI_TASK_NEEDS[task].map((n) => (
                          <Badge key={n} tone="accent">
                            {t(aiNeedLabelKey(n))}
                          </Badge>
                        ))}
                      </div>
                    )}
                  </td>
                  <td className="min-w-[16rem] px-3.5 py-2.5">
                    <SelectField<string>
                      testId={`admin-ai-task-profile-${task}`}
                      aria-label={t(aiTaskNameKey(task))}
                      items={optionsFor(task)}
                      value={profileId == null ? NONE : String(profileId)}
                      onChange={(v) => assign(task, v)}
                      disabled={saveM.isPending}
                    />
                    {selected && selected.valid_id !== 1 && (
                      <p className="mt-1 text-xs text-escalation">
                        {t("admin.ai.tasks.selectedInactive", {
                          fallback: t(aiTaskFallbackKey(task)),
                        })}
                      </p>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {status && (
        <p
          className={status.ok ? "text-sm text-muted" : "text-sm text-danger"}
          data-testid="admin-ai-tasks-status"
        >
          {status.text}
        </p>
      )}
    </div>
  );
}
