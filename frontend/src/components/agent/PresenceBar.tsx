import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { presenceQueryKey } from "@/lib/useSSE";

/**
 * "X is viewing / composing" chips for the currently open ticket.
 *
 * Polled via TanStack Query (`refetchInterval`) as a baseline, and also
 * refetched immediately whenever an SSE `presence_changed` message
 * invalidates `presenceQueryKey(ticketId)` (see useSSE.ts) — the poll
 * interval is just a fallback for agents who, for whatever reason, aren't
 * getting the SSE push.
 */
export function PresenceBar({
  ticketId,
  selfUserId,
}: {
  ticketId: number;
  selfUserId?: number;
}) {
  const { t } = useTranslation();
  const presenceQ = useQuery({
    queryKey: presenceQueryKey(ticketId),
    queryFn: () => api.getPresence(ticketId),
    refetchInterval: 15000,
  });

  const others = (presenceQ.data ?? []).filter((p) => p.user_id !== selfUserId);
  if (others.length === 0) return null;

  return (
    <div className="flex flex-wrap items-center gap-1.5" data-testid="presence-bar">
      {others.map((p) => (
        <span key={`${p.user_id}-${p.mode}`} data-testid={`presence-chip-${p.user_id}`}>
          <Badge tone={p.mode === "composing" ? "warn" : "accent"}>
            {p.name} ·{" "}
            {p.mode === "composing" ? t("ticket.presenceComposing") : t("ticket.presenceViewing")}
          </Badge>
        </span>
      ))}
    </div>
  );
}
