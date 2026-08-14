import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";

/**
 * Shared state for an article's AI-origin trace: whether it is expanded and
 * (once expanded) the lazily-fetched `GET .../ai-origin` result. Callers
 * render the toggle (`AiOriginToggle`) and the trace block (`AiOriginTrace`,
 * both in `AiOriginBadge.tsx`) in separate places — e.g. a compact badge in
 * a meta row plus a full-width block below the article/bubble — while
 * sharing one query.
 */
export function useAiOriginTrace({
  ticketId,
  articleId,
}: {
  ticketId: number;
  articleId: number;
}) {
  const [open, setOpen] = useState(false);
  const query = useQuery({
    queryKey: ["tickets", ticketId, "articles", articleId, "ai-origin"],
    queryFn: () => api.getArticleAiOrigin(ticketId, articleId),
    enabled: open,
  });
  return { open, toggle: () => setOpen((o) => !o), query };
}
