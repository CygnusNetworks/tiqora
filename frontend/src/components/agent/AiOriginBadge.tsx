import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { ToolTraceCard } from "@/components/ai/ToolResultView";

/**
 * Badge for an article that an AI agent auto-sent (never a draft — those get
 * their own tool trace inline in `AiPanel`). Lazily fetches
 * `GET .../ai-origin` on first expand and reuses `ToolTraceCard` (same
 * renderer as the draft panel) so both surfaces stay visually identical.
 */
export function AiOriginBadge({
  ticketId,
  articleId,
  compact = false,
}: {
  ticketId: number;
  articleId: number;
  /** Icon-only rendering for tight spaces (conversation bubbles). */
  compact?: boolean;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const originQ = useQuery({
    queryKey: ["tickets", ticketId, "articles", articleId, "ai-origin"],
    queryFn: () => api.getArticleAiOrigin(ticketId, articleId),
    enabled: open,
  });

  return (
    <span className="inline-flex flex-col items-start gap-1">
      <button
        type="button"
        aria-expanded={open}
        data-testid={`ai-origin-badge-${articleId}`}
        onClick={() => setOpen((o) => !o)}
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
      {open && (
        <span
          className="block w-full min-w-[16rem] max-w-sm"
          data-testid={`ai-origin-trace-${articleId}`}
        >
          {originQ.isLoading && (
            <span className="text-[11px] text-muted">…</span>
          )}
          {originQ.data && (originQ.data.tool_trace?.length ?? 0) > 0 && (
            <span className="block space-y-1.5">
              <span className="block text-[11px] font-medium text-muted">
                {t("ticket.ai.toolTrace", {
                  count: originQ.data.tool_trace?.length ?? 0,
                })}
              </span>
              <ul className="space-y-1.5">
                {originQ.data.tool_trace?.map((step, i) => (
                  <li key={i}>
                    <ToolTraceCard
                      name={step.name}
                      content={step.content}
                      testId={`ai-origin-trace-step-${articleId}-${i}`}
                    />
                  </li>
                ))}
              </ul>
            </span>
          )}
          {originQ.data && !(originQ.data.tool_trace?.length ?? 0) && (
            <span className="text-[11px] text-muted">
              {t("ticket.ai.originTraceEmpty")}
            </span>
          )}
        </span>
      )}
    </span>
  );
}
