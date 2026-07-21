import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { Avatar } from "@/components/ui/Avatar";
import { UsersIcon } from "@/components/ui/icons";
import { cn } from "@/lib/cn";

/** Cache key for the global online-agents list. */
const onlineAgentsQueryKey = ["agents", "online"] as const;

/** Poll interval for GET /agents/online (~20–30s). */
const ONLINE_REFETCH_MS = 25_000;

/** Heartbeat interval for POST /agents/presence/ping (inside the 60s TTL). */
const PING_INTERVAL_MS = 45_000;

/**
 * Header control: badge with online-agent count + popover listing avatars
 * and names. Polls `GET /agents/online` and keeps the session marked online
 * via a light presence ping while the shell is mounted.
 */
export function OnlineAgentsPopover() {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  const onlineQ = useQuery({
    queryKey: onlineAgentsQueryKey,
    queryFn: ({ signal }) => api.getOnlineAgents(signal),
    refetchInterval: ONLINE_REFETCH_MS,
    // Refetch when the popover opens so the list is fresh.
    refetchOnWindowFocus: true,
  });

  // Idle-but-open: keep the Redis TTL alive even without other API traffic.
  useEffect(() => {
    let cancelled = false;
    const ping = () => {
      void api.pingOnlinePresence().catch(() => {
        /* best-effort */
      });
    };
    ping();
    const id = window.setInterval(() => {
      if (!cancelled) ping();
    }, PING_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  // When the panel opens, force a refetch so the list isn't stale.
  useEffect(() => {
    if (open) {
      void onlineQ.refetch();
    }
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps -- only on open

  const agents = onlineQ.data ?? [];
  const count = agents.length;

  return (
    <div className="relative">
      <button
        type="button"
        data-testid="online-agents-trigger"
        aria-label={t("onlineAgents.title")}
        aria-expanded={open}
        aria-haspopup="dialog"
        title={t("onlineAgents.title")}
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "relative flex h-8 w-8 items-center justify-center rounded-lg text-ink/70 transition-colors duration-100 hover:bg-surface-subtle hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
          open && "bg-surface-subtle text-ink",
        )}
      >
        <UsersIcon className="text-[17px]" />
        {count > 0 && (
          <span
            data-testid="online-agents-count"
            className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-accent px-1 text-[9px] font-bold tabular-nums text-accent-ink"
          >
            {count > 99 ? "99+" : count}
          </span>
        )}
      </button>

      {open && (
        <>
          <button
            type="button"
            aria-hidden
            tabIndex={-1}
            className="fixed inset-0 z-30 cursor-default"
            onClick={() => setOpen(false)}
          />
          <div
            data-testid="online-agents-panel"
            role="dialog"
            aria-label={t("onlineAgents.title")}
            className="absolute right-0 z-40 mt-1.5 w-72 max-w-[90vw] overflow-hidden rounded-lg border border-hairline bg-surface shadow-xl"
          >
            <div className="border-b border-hairline px-3 py-2 text-[12px] font-semibold text-ink">
              {t("onlineAgents.title")}
              {count > 0 && (
                <span className="ml-1.5 font-normal tabular-nums text-muted">
                  ({count})
                </span>
              )}
            </div>
            {agents.length === 0 ? (
              <p
                className="px-3 py-6 text-center text-[12.5px] text-muted"
                data-testid="online-agents-empty"
              >
                {t("onlineAgents.empty")}
              </p>
            ) : (
              <ul className="max-h-80 list-none overflow-y-auto" data-testid="online-agents-list">
                {agents.map((agent) => {
                  const initials = (
                    (agent.full_name?.trim()?.[0] ?? agent.login[0] ?? "?") +
                    (agent.full_name?.trim()?.split(/\s+/).slice(-1)[0]?.[0] ?? "")
                  ).toUpperCase();
                  return (
                    <li
                      key={agent.id}
                      data-testid={`online-agent-${agent.id}`}
                      className="flex items-center gap-2.5 border-b border-hairline px-3 py-2 last:border-b-0"
                    >
                      <span className="relative shrink-0">
                        <Avatar
                          avatarUrl={agent.avatar_url}
                          initials={initials.slice(0, 2)}
                          size={28}
                          testId={`online-agent-avatar-${agent.id}`}
                        />
                        <span
                          className="absolute bottom-0 right-0 h-2 w-2 rounded-full border border-surface bg-green"
                          aria-hidden
                        />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[12.5px] font-medium text-ink">
                          {agent.full_name || agent.login}
                        </span>
                        <span className="block truncate text-[11px] text-muted">{agent.login}</span>
                      </span>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </>
      )}
    </div>
  );
}
