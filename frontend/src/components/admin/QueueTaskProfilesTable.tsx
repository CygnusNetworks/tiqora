import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import type { LlmProfileOut } from "@/lib/aiApi";
import {
  AI_TASKS,
  AI_TASKS_FALLING_BACK_TO_AGENT,
  aiTaskDescriptionKey,
  aiTaskFallbackKey,
  aiTaskNameKey,
  resolveTaskProfile,
  type AiTask,
} from "@/lib/aiTasks";
import { taskProfileItems } from "@/components/admin/ai-models/shared";
import { Badge } from "@/components/ui/Badge";
import { SelectField } from "@/components/ui/SelectField";
import type { SelectMenuItem } from "@/components/ui/SelectMenu";

const INHERIT = "inherit";
const NONE = "none";

/** This queue's overrides: task absent = inherits the global default,
 * `null` = "Kein eigenes Profil", number = own profile. */
export type QueueTaskOverrides = Map<AiTask, number | null>;

/**
 * "Modelle" block of the queue editor: one row per AI task with the profile
 * this queue uses. The first option inherits the global assignment (named
 * after it), then "Kein eigenes Profil" with the task's fallback, then every
 * profile — disabled when one of its models lacks what the task needs.
 */
export function QueueTaskProfilesTable({
  value,
  onChange,
  profiles,
  globalDefaults,
}: {
  value: QueueTaskOverrides;
  onChange: (next: QueueTaskOverrides) => void;
  profiles: LlmProfileOut[];
  globalDefaults: Map<AiTask, number | null>;
}) {
  const { t } = useTranslation();
  const byId = new Map(profiles.map((p) => [p.id, p]));
  const isActive = (id: number) => byId.get(id)?.valid_id === 1;

  const set = (task: AiTask, choice: string) => {
    const next = new Map(value);
    if (choice === INHERIT) next.delete(task);
    else next.set(task, choice === NONE ? null : Number(choice));
    onChange(next);
  };

  const optionsFor = (task: AiTask): SelectMenuItem<string>[] => {
    const fallback = t(aiTaskFallbackKey(task));
    const global = byId.get(globalDefaults.get(task) ?? -1);
    return [
      {
        value: INHERIT,
        label: global
          ? t("admin.ai.queues.models.inheritProfile", { name: global.name })
          : t("admin.ai.queues.models.inheritNone", { fallback }),
        hint:
          global && global.valid_id !== 1
            ? t("admin.ai.tasks.profileInactive")
            : undefined,
      },
      { value: NONE, label: t("admin.ai.tasks.noProfile", { fallback }) },
      ...taskProfileItems(task, profiles, t),
    ];
  };

  /** What the task runs on right now, following the backend's resolution. */
  const renderEffective = (task: AiTask) => {
    const id = resolveTaskProfile(task, globalDefaults, value, isActive);
    const profile = id != null ? byId.get(id) : undefined;
    if (!profile) {
      // triage/summary/refine without any profile end where the agent ends.
      const via = AI_TASKS_FALLING_BACK_TO_AGENT.has(task) ? "agent" : task;
      return <span className="text-xs text-muted">{t(aiTaskFallbackKey(via))}</span>;
    }
    // Resolved to something other than the task's own level → the agent's.
    const ownLevel = value.has(task) ? value.get(task) : globalDefaults.get(task);
    const own = ownLevel === id;
    // Static hint: entries carry only the model's validity, not its provider's
    // (a disabled provider is skipped by the backend but not visible here).
    const first = profile.entries.find((e) => e.valid_id === 1) ?? profile.entries[0];
    return (
      <div className="min-w-0">
        <div className="truncate text-ink">
          {profile.name}
          {!own && (
            <span className="ml-1 text-xs text-muted">
              ({t("admin.ai.queues.models.viaAgent", { task: t(aiTaskNameKey("agent")) })})
            </span>
          )}
        </div>
        {first && (
          <div className="truncate text-xs text-muted">
            {first.model_label} @ {first.provider_name}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="space-y-2" data-testid="admin-ai-queue-models-section">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">
          {t("admin.ai.queues.models.title")}
        </h3>
        <Link to="/admin/ai/models" className="text-xs text-accent hover:underline">
          {t("admin.ai.queues.models.manage")}
        </Link>
      </div>
      <p className="text-xs text-muted">{t("admin.ai.queues.models.intro")}</p>
      <div className="overflow-x-auto rounded-lg border border-hairline">
        <table className="w-full text-sm" data-testid="admin-ai-queue-models-table">
          <thead>
            <tr className="border-b border-hairline bg-surface-subtle text-left text-[11px] uppercase tracking-wide text-muted">
              <th className="px-3 py-2 font-semibold">{t("admin.ai.queues.models.colTask")}</th>
              <th className="px-3 py-2 font-semibold">{t("admin.ai.queues.models.colProfile")}</th>
              <th className="px-3 py-2 font-semibold">{t("admin.ai.queues.models.colEffective")}</th>
            </tr>
          </thead>
          <tbody>
            {AI_TASKS.map((task) => {
              const overridden = value.has(task);
              const own = value.get(task);
              return (
                <tr
                  key={task}
                  data-testid={`admin-ai-queue-task-row-${task}`}
                  className="border-t border-hairline first:border-t-0"
                >
                  <td className="px-3 py-2 align-top">
                    <div className="font-medium text-ink">{t(aiTaskNameKey(task))}</div>
                    <div className="text-xs text-muted">{t(aiTaskDescriptionKey(task))}</div>
                  </td>
                  <td className="min-w-[15rem] px-3 py-2 align-top">
                    <div className="flex items-center gap-2">
                      <div className="min-w-0 flex-1">
                        <SelectField<string>
                          testId={`admin-ai-queue-task-profile-${task}`}
                          aria-label={t(aiTaskNameKey(task))}
                          items={optionsFor(task)}
                          value={!overridden ? INHERIT : own == null ? NONE : String(own)}
                          onChange={(v) => set(task, v)}
                        />
                      </div>
                      <span data-testid={`admin-ai-queue-task-state-${task}`}>
                        {overridden ? (
                          <Badge tone="accent">{t("admin.ai.queues.models.overridden")}</Badge>
                        ) : (
                          <Badge tone="muted">{t("admin.ai.queues.models.inherited")}</Badge>
                        )}
                      </span>
                    </div>
                  </td>
                  <td
                    className="px-3 py-2 align-top"
                    data-testid={`admin-ai-queue-task-effective-${task}`}
                  >
                    {renderEffective(task)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
