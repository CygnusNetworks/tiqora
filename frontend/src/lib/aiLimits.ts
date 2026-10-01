import { useQuery } from "@tanstack/react-query";
import { aiApi, type AiLimitOut } from "@/lib/aiApi";

/** Query key of the budget status — shared by the admin bell, the queue
 * policy list and the SSE `ai_limit_changed` invalidation. */
export const AI_LIMITS_QUERY_KEY = ["admin", "ai", "limits"] as const;

/** Token budgets are entered and shown in millions of tokens. */
export const TOKENS_PER_MIO = 1_000_000;

/**
 * Budget status of every AI cap (queue token budgets, provider cost budgets).
 * Admin-only endpoint — pass `enabled: false` for everyone else. Refreshed
 * by the SSE `ai_limit_changed` notice; the interval covers window resets
 * (a new day frees the budget without any event).
 */
export function useAiLimits(enabled: boolean) {
  return useQuery({
    queryKey: AI_LIMITS_QUERY_KEY,
    queryFn: ({ signal }) => aiApi.listLimits(signal),
    enabled,
    refetchInterval: 5 * 60_000,
    staleTime: 60_000,
  });
}

/** `1234567` → `"1,23"` (de) — millions, at most two decimals. */
export function formatMio(tokens: number, locale: string): string {
  return new Intl.NumberFormat(locale, { maximumFractionDigits: 2 }).format(
    tokens / TOKENS_PER_MIO,
  );
}

/** Spend vs. cap in the limit's own unit, e.g. `"3,2 / 3"` (Mio.) or
 * `"1,02 / 1 USD"`. */
export function formatLimitAmount(limit: AiLimitOut, locale: string): string {
  if (limit.kind === "queue_tokens_day") {
    return `${formatMio(limit.used, locale)} / ${formatMio(limit.limit, locale)}`;
  }
  const fmt = new Intl.NumberFormat(locale, { maximumFractionDigits: 2 });
  const unit = limit.currency ? ` ${limit.currency}` : "";
  return `${fmt.format(limit.used)} / ${fmt.format(limit.limit)}${unit}`;
}
