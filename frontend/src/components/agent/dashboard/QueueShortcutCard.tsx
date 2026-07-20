import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import type { QueueNode } from "@/lib/api";

/**
 * Dashboard queue shortcut: the queue name plus two independently clickable
 * badges — an "open" count (neutral) jumping to the open view and, only when
 * there are unclaimed arrivals, an accent-tinted "new" pill jumping to the new
 * view. Reuses the sidebar's badge language (accent-dim pill = "has new").
 * Rendered as sibling links (not nested anchors) so each is its own target.
 */
export function QueueShortcutCard({ queue }: { queue: QueueNode }) {
  const { t } = useTranslation();
  const open = queue.counts?.open ?? 0;
  const newCount = queue.counts?.new ?? 0;
  const shortName = queue.name.includes("::") ? queue.name.split("::").pop()! : queue.name;
  const openSearch = { queue_id: queue.id, state_type: "open" as const };

  return (
    <div
      className="flex flex-col gap-2.5 rounded-lg border border-hairline bg-surface p-4 transition-colors duration-100 hover:border-accent/60"
      data-testid={`queue-shortcut-${queue.id}`}
    >
      <Link
        to="/agent/queues"
        search={openSearch}
        className="truncate text-xs font-medium uppercase tracking-wide text-muted transition-colors hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
        title={queue.name}
      >
        {shortName}
      </Link>
      <div className="flex items-baseline gap-2">
        <Link
          to="/agent/queues"
          search={openSearch}
          data-testid={`queue-shortcut-${queue.id}-open`}
          className="inline-flex items-baseline gap-1.5 rounded-md px-1 -mx-1 transition-colors hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
        >
          <span className="font-mono text-2xl font-semibold tabular-nums text-ink">{open}</span>
          <span className="text-xs text-muted">{t("dashboard.openCount")}</span>
        </Link>
        {newCount > 0 && (
          <Link
            to="/agent/queues"
            search={{ queue_id: queue.id, state_type: "new" }}
            data-testid={`queue-shortcut-${queue.id}-new`}
            className="ml-auto shrink-0 rounded-full bg-accent-dim px-2 py-0.5 font-mono text-xs font-semibold tabular-nums text-accent transition-colors hover:brightness-95 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
            title={t("queue.newCount", { count: newCount })}
          >
            +{newCount} {t("dashboard.newLabel")}
          </Link>
        )}
      </div>
    </div>
  );
}
