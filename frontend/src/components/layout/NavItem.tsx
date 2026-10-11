import type { ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/cn";

/** Count badge shared by the nav items and, in spirit, the queue rows: shows
 * a single number (the open count) and signals "has new items" by colour
 * alone — accent-tinted pill instead of plain muted text, no "neu" chip. */
export function NavCountBadge({
  count,
  newCount,
  alertCount,
}: {
  count: number;
  newCount?: number;
  /** SLA-escalated tickets within this entry — shown as a separate red pill. */
  alertCount?: number;
}) {
  const { t } = useTranslation();
  const hasNew = (newCount ?? 0) > 0;
  const badge = (
    <span
      className={cn(
        "shrink-0 rounded-full px-1.5 py-0.5 font-mono text-[11px] tabular-nums",
        hasNew ? "bg-accent-dim font-semibold text-accent" : "text-muted",
      )}
      title={hasNew ? t("queue.newCount", { count: newCount }) : undefined}
    >
      {count}
    </span>
  );
  if (!alertCount) return badge;
  return (
    <span className="flex shrink-0 items-center gap-1">
      <span
        className="rounded-full bg-danger/15 px-1.5 py-0.5 font-mono text-[11px] font-semibold tabular-nums text-danger"
        title={t("sidebar.escalatedCount", { count: alertCount })}
        data-testid="nav-escalated-count"
      >
        {alertCount}
      </span>
      {badge}
    </span>
  );
}

export function NavItem({
  to,
  search,
  label,
  icon,
  count,
  newCount,
  alertCount,
  testId,
  onNavigate,
  disabled,
  exact,
  active,
}: {
  to: string;
  search?: Record<string, unknown>;
  label: string;
  icon?: ReactNode;
  count?: number;
  newCount?: number;
  alertCount?: number;
  testId: string;
  onNavigate?: () => void;
  disabled?: boolean;
  exact?: boolean;
  /** Overrides Link's own path/search matching. The ticket-list entries all
   * share `/agent/queues` and differ only in search params, which Link
   * matches as a subset — "Eingang" would light up on every one of them. */
  active?: boolean;
}) {
  const iconNode = icon && (
    <span className="flex h-4 w-4 shrink-0 items-center justify-center text-muted [&_svg]:h-4 [&_svg]:w-4 group-aria-[current=page]:text-accent">
      {icon}
    </span>
  );
  if (disabled) {
    return (
      <span
        className="flex cursor-not-allowed items-center gap-2.5 rounded-lg px-2.5 py-[7px] text-[13.5px] text-muted/50"
        data-testid={testId}
        aria-disabled="true"
      >
        {iconNode}
        <span className="flex-1">{label}</span>
        {count != null && <span className="font-mono text-[11px] tabular-nums">{count}</span>}
      </span>
    );
  }
  const base =
    "group flex items-center gap-2.5 rounded-lg px-2.5 py-[7px] text-[13.5px] transition-colors duration-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";
  const idle = "text-ink/80 hover:bg-surface-subtle";
  const current = "font-medium text-ink bg-accent-dim shadow-[inset_2px_0_0_var(--color-accent)]";
  const content = (
    <>
      {iconNode}
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {count != null && (
        <NavCountBadge count={count} newCount={newCount} alertCount={alertCount} />
      )}
    </>
  );
  if (active !== undefined) {
    return (
      <Link
        to={to}
        search={search}
        onClick={onNavigate}
        data-testid={testId}
        className={cn(base, active ? current : idle)}
        // Link's own matching would treat these search params as a subset
        // ("Eingang" = any /agent/queues). Make it exact so its aria-current
        // agrees with ours, and pin ours for the inactive case.
        activeOptions={{ exact: true, includeSearch: true }}
        inactiveProps={{ "aria-current": active ? "page" : undefined }}
      >
        {content}
      </Link>
    );
  }
  return (
    <Link
      to={to}
      search={search}
      onClick={onNavigate}
      data-testid={testId}
      activeOptions={{ exact }}
      className={base}
      activeProps={{ className: current, "aria-current": "page" }}
      inactiveProps={{ className: idle }}
    >
      {content}
    </Link>
  );
}
