import type { QueryClient } from "@tanstack/react-query";
import { ApiError } from "@/lib/api";

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
