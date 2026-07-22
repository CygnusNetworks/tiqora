import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { statusColor } from "@/lib/daemonStatus";
import { AdminCommandPalette } from "@/components/admin/AdminCommandPalette";
import { SearchIcon, UsersIcon, TicketIcon, MailIcon, BoltIcon, ServerIcon } from "@/components/ui/icons";
import { ADMIN_PAGE_GROUPS, adminPagesByGroup, type AdminPageGroup } from "@/lib/adminSearch";
import { cn } from "@/lib/cn";

const GROUP_META: Record<AdminPageGroup, { titleKey: string; Icon: typeof UsersIcon }> = {
  access: { titleKey: "admin.group.access", Icon: UsersIcon },
  tickets: { titleKey: "admin.group.tickets", Icon: TicketIcon },
  communication: { titleKey: "admin.group.communication", Icon: MailIcon },
  automation: { titleKey: "admin.group.automation", Icon: BoltIcon },
  system: { titleKey: "admin.group.system", Icon: ServerIcon },
};

const STALE_TIME_MS = 5 * 60_000;
const DAEMONS_QUERY_KEY = ["admin", "daemons"] as const;
const DAEMONS_REFETCH_INTERVAL_MS = 30_000;

function KpiCard({
  to,
  Icon,
  label,
  value,
  hint,
  tone,
  testId,
}: {
  to: string;
  Icon: typeof UsersIcon;
  label: string;
  value: string;
  hint?: string;
  tone?: "danger";
  testId: string;
}) {
  return (
    <Link
      to={to}
      data-testid={testId}
      className="rounded-lg border border-hairline bg-surface p-4 transition-colors duration-100 hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
    >
      <div className="flex items-center gap-2 text-muted">
        <Icon className="h-4 w-4 shrink-0" />
        <p className="truncate text-xs uppercase tracking-wide">{label}</p>
      </div>
      <p
        className={cn(
          "mt-2 font-mono text-2xl font-semibold tabular-nums",
          tone === "danger" ? "text-danger" : "text-ink",
        )}
      >
        {value}
      </p>
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </Link>
  );
}

export function AdminHomePage() {
  const { t } = useTranslation();
  const [searchOpen, setSearchOpen] = useState(false);

  const usersQ = useQuery({
    queryKey: ["admin", "dashboard", "users"],
    queryFn: ({ signal }) => api.adminUsers.list({ page: 1, pageSize: 1 }, signal),
    staleTime: STALE_TIME_MS,
  });

  const queuesQ = useQuery({
    queryKey: ["admin", "dashboard", "queues"],
    queryFn: ({ signal }) => api.adminQueues.list({ page: 1, pageSize: 1 }, signal),
    staleTime: STALE_TIME_MS,
  });

  const daemonsQ = useQuery({
    queryKey: DAEMONS_QUERY_KEY,
    queryFn: ({ signal }) => api.getDaemons(signal),
    staleTime: STALE_TIME_MS,
    refetchInterval: DAEMONS_REFETCH_INTERVAL_MS,
  });

  const services = daemonsQ.data?.services ?? [];
  const nowMs = Date.now();
  const activeDaemons = services.filter((s) => s.enabled).length;
  const erroredDaemons = services.filter((s) => statusColor(s, nowMs) === "red").length;

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4" data-testid="admin-home-page">
      <div>
        <h1 className="font-display text-xl font-semibold text-ink">{t("admin.title")}</h1>
        <button
          type="button"
          data-testid="admin-dashboard-search-trigger"
          onClick={() => setSearchOpen(true)}
          className="mt-3 flex w-full max-w-md items-center gap-2 rounded-lg border border-hairline bg-surface px-3 py-2 text-left text-sm text-muted transition-colors duration-100 hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
        >
          <SearchIcon className="h-4 w-4 shrink-0" />
          <span className="flex-1 truncate">{t("admin.commandPalette.placeholder")}</span>
          <kbd className="shrink-0 rounded border border-hairline bg-surface-subtle px-1 text-[10px] font-medium text-muted">
            ⌘K
          </kbd>
        </button>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <KpiCard
          to="/admin/users"
          Icon={UsersIcon}
          label={t("admin.dashboard.agents")}
          value={usersQ.isLoading ? "…" : String(usersQ.data?.total ?? "—")}
          testId="admin-kpi-agents"
        />
        <KpiCard
          to="/admin/queues"
          Icon={TicketIcon}
          label={t("admin.dashboard.queues")}
          value={queuesQ.isLoading ? "…" : String(queuesQ.data?.total ?? "—")}
          testId="admin-kpi-queues"
        />
        <KpiCard
          to="/admin/daemons"
          Icon={ServerIcon}
          label={t("admin.dashboard.daemons")}
          value={
            daemonsQ.isLoading
              ? "…"
              : t("admin.dashboard.daemonsActive", { active: activeDaemons, total: services.length })
          }
          hint={erroredDaemons > 0 ? t("admin.dashboard.daemonsErrors", { count: erroredDaemons }) : undefined}
          tone={erroredDaemons > 0 ? "danger" : undefined}
          testId="admin-kpi-daemons"
        />
      </div>

      <div className="space-y-5">
        {ADMIN_PAGE_GROUPS.map((group) => {
          const { titleKey, Icon } = GROUP_META[group];
          return (
            <section key={group} data-testid={`admin-home-group-${group}`}>
              <div className="mb-2 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-muted">
                <Icon className="h-3.5 w-3.5 shrink-0" />
                <h2>{t(titleKey)}</h2>
              </div>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {adminPagesByGroup(group).map((page) => (
                  <Link
                    key={page.slug}
                    to={page.route}
                    data-testid={`admin-home-link-${page.slug}`}
                    className="rounded-lg border border-hairline bg-surface px-3 py-2.5 text-sm text-ink transition-colors duration-100 hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
                  >
                    <div className="flex items-center gap-2 font-medium">
                      <Icon className="h-3.5 w-3.5 shrink-0 text-muted" />
                      {t(page.nameKey)}
                    </div>
                    <p className="mt-0.5 text-xs text-muted">{t(page.descriptionKey)}</p>
                  </Link>
                ))}
              </div>
            </section>
          );
        })}
      </div>

      <AdminCommandPalette open={searchOpen} onClose={() => setSearchOpen(false)} />
    </div>
  );
}
