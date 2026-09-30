import type { QueryClient } from "@tanstack/react-query";
import type { TFunction } from "i18next";
import { ApiError } from "@/lib/api";
import type { LlmProfileOut } from "@/lib/aiApi";
import { aiNeedMissingKey, missingNeeds, type AiTask } from "@/lib/aiTasks";
import type { SelectMenuItem } from "@/components/ui/SelectMenu";

export const PROVIDERS_KEY = ["admin", "ai", "providers"] as const;
export const MODELS_KEY = ["admin", "ai", "models"] as const;
export const PROFILES_KEY = ["admin", "ai", "profiles"] as const;
export const TASK_DEFAULTS_KEY = ["admin", "ai", "task-defaults"] as const;
export const POLICIES_KEY = ["admin", "ai", "queue-policies"] as const;
export const QUEUES_KEY = ["admin", "ai", "reference-queues"] as const;
export const SETTINGS_KEY = ["admin", "ai", "settings"] as const;

/** Models, profiles and assignments reference each other (labels in profile
 * entries, `used_in_profiles`, `used_by`) — refresh all of them after any
 * write so no tab shows a stale cross-reference. */
export function invalidateCatalog(qc: QueryClient) {
  return Promise.all(
    [MODELS_KEY, PROFILES_KEY, TASK_DEFAULTS_KEY].map((queryKey) =>
      qc.invalidateQueries({ queryKey }),
    ),
  );
}

/** The backend's `detail` is a German sentence meant to be shown as is; a
 * pydantic 422 (list of field errors) falls back to *fallback*. */
export function errorText(err: unknown, fallback: string): string {
  if (err instanceof ApiError && !err.message.includes("[object ")) {
    return err.message;
  }
  return fallback;
}

/** "12,5" / "12.5" / "" → number or null (negative/garbage → null). */
export function numberOrNull(v: unknown): number | null {
  if (typeof v === "number") return Number.isFinite(v) && v >= 0 ? v : null;
  const s = String(v ?? "").trim();
  if (!s) return null;
  const n = Number(s.replace(",", "."));
  return Number.isFinite(n) && n >= 0 ? n : null;
}

export function formatPrice(
  input: number | null,
  output: number | null,
  currency: string | null,
): string | null {
  if (input == null && output == null) return null;
  return `${input ?? "–"} / ${output ?? "–"} ${currency ?? ""}`.trim();
}

/** One select option per profile for *task* — shared by the global task
 * table and the queue editor. A profile with a model that lacks one of the
 * task's needs is shown but disabled (the backend would reject it), with the
 * reason as hint; a disabled profile is marked as such. */
export function taskProfileItems(
  task: AiTask,
  profiles: LlmProfileOut[],
  t: TFunction,
): SelectMenuItem<string>[] {
  return profiles.map((p) => {
    const missing = missingNeeds(task, p.entries);
    return {
      value: String(p.id),
      label: p.name,
      disabled: missing.length > 0,
      hint:
        missing.length > 0
          ? missing.map((n) => t(aiNeedMissingKey(n))).join(", ")
          : p.valid_id !== 1
            ? t("admin.ai.tasks.profileInactive")
            : undefined,
    };
  });
}
