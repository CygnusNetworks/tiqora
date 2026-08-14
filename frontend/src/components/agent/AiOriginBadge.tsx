import type { UseQueryResult } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import type { AiOriginOut } from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { ToolTraceCard } from "@/components/ai/ToolResultView";

/** Clickable 🤖 badge (or icon-only in `compact` mode) that toggles the
 * shared open state returned by `useAiOriginTrace`. */
export function AiOriginToggle({
  articleId,
  compact = false,
  open,
  onToggle,
}: {
  articleId: number;
  /** Icon-only rendering for tight spaces (conversation bubbles). */
  compact?: boolean;
  open: boolean;
  onToggle: () => void;
}) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      aria-expanded={open}
      data-testid={`ai-origin-badge-${articleId}`}
      onClick={onToggle}
      title={t("ticket.ai.originBadgeTooltip")}
      className="inline-flex"
    >
      {compact ? (
        <span aria-hidden className="text-[11px]">
          🤖
        </span>
      ) : (
        <Badge tone="accent">🤖 {t("ticket.ai.originBadge")}</Badge>
      )}
    </button>
  );
}

/** Full-width tool-trace block (same `ToolTraceCard` renderer as the draft
 * panel), rendered outside the badge/meta row so its key/value grids get the
 * article's full width instead of being squeezed into a narrow span. Renders
 * nothing while collapsed. */
export function AiOriginTrace({
  articleId,
  open,
  query,
}: {
  articleId: number;
  open: boolean;
  query: UseQueryResult<AiOriginOut>;
}) {
  const { t } = useTranslation();
  if (!open) return null;
  const trace = query.data?.tool_trace;
  return (
    <div className="w-full space-y-1.5" data-testid={`ai-origin-trace-${articleId}`}>
      {query.isLoading && <p className="text-xs text-muted">…</p>}
      {query.data && (trace?.length ?? 0) > 0 && (
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-muted">
            {t("ticket.ai.toolTrace", { count: trace?.length ?? 0 })}
          </p>
          <ul className="space-y-1.5">
            {trace?.map((step, i) => (
              <li key={i}>
                <ToolTraceCard
                  name={step.name}
                  content={step.content}
                  testId={`ai-origin-trace-step-${articleId}-${i}`}
                />
              </li>
            ))}
          </ul>
        </div>
      )}
      {query.data && !(trace?.length ?? 0) && (
        <p className="text-xs text-muted">{t("ticket.ai.originTraceEmpty")}</p>
      )}
    </div>
  );
}
