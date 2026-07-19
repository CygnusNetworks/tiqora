import { useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";
import { useTheme } from "@/themes/theme";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/cn";

type NavLink = { to: string; labelKey: string; testId: string };
type NavGroup = { titleKey: string; links: NavLink[] };

const NAV_GROUPS: NavGroup[] = [
  {
    titleKey: "admin.group.usersPermissions",
    links: [
      { to: "/admin/users", labelKey: "admin.nav.users", testId: "admin-nav-users" },
      { to: "/admin/groups", labelKey: "admin.nav.groups", testId: "admin-nav-groups" },
      { to: "/admin/roles", labelKey: "admin.nav.roles", testId: "admin-nav-roles" },
    ],
  },
  {
    titleKey: "admin.group.ticketConfig",
    links: [
      { to: "/admin/queues", labelKey: "admin.nav.queues", testId: "admin-nav-queues" },
      { to: "/admin/states", labelKey: "admin.nav.states", testId: "admin-nav-states" },
      {
        to: "/admin/priorities",
        labelKey: "admin.nav.priorities",
        testId: "admin-nav-priorities",
      },
    ],
  },
  {
    titleKey: "admin.group.customers",
    links: [
      {
        to: "/admin/customer-users",
        labelKey: "admin.nav.customerUsers",
        testId: "admin-nav-customer-users",
      },
      {
        to: "/admin/customer-companies",
        labelKey: "admin.nav.customerCompanies",
        testId: "admin-nav-customer-companies",
      },
    ],
  },
  {
    titleKey: "admin.group.communication",
    links: [
      { to: "/admin/templates", labelKey: "admin.nav.templates", testId: "admin-nav-templates" },
      {
        to: "/admin/salutations",
        labelKey: "admin.nav.salutations",
        testId: "admin-nav-salutations",
      },
      {
        to: "/admin/signatures",
        labelKey: "admin.nav.signatures",
        testId: "admin-nav-signatures",
      },
      {
        to: "/admin/auto-responses",
        labelKey: "admin.nav.autoResponses",
        testId: "admin-nav-auto-responses",
      },
    ],
  },
  {
    titleKey: "admin.group.automation",
    links: [
      {
        to: "/admin/postmaster-filters",
        labelKey: "admin.nav.postmasterFilters",
        testId: "admin-nav-postmaster-filters",
      },
      { to: "/admin/acl", labelKey: "admin.nav.acl", testId: "admin-nav-acl" },
      {
        to: "/admin/generic-agent-jobs",
        labelKey: "admin.nav.genericAgentJobs",
        testId: "admin-nav-generic-agent-jobs",
      },
    ],
  },
  {
    titleKey: "admin.group.system",
    links: [
      {
        to: "/admin/dynamic-fields",
        labelKey: "admin.nav.dynamicFields",
        testId: "admin-nav-dynamic-fields",
      },
    ],
  },
];

function SidebarNav({ onNavigate }: { onNavigate?: () => void }) {
  const { t } = useTranslation();
  return (
    <nav className="flex flex-col gap-4" data-testid="admin-sidebar-nav">
      {NAV_GROUPS.map((group) => (
        <div key={group.titleKey}>
          <h2 className="mb-1 px-2 text-xs font-semibold uppercase tracking-wide text-muted">
            {t(group.titleKey)}
          </h2>
          <ul className="list-none space-y-0.5">
            {group.links.map((link) => (
              <li key={link.to}>
                <Link
                  to={link.to}
                  data-testid={link.testId}
                  onClick={onNavigate}
                  className="block rounded px-2 py-1.5 text-sm text-ink transition-colors duration-100 hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
                  activeProps={{ className: "bg-surface-subtle font-medium text-accent" }}
                >
                  {t(link.labelKey)}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  );
}

export function AdminShell({ children }: { children: ReactNode }) {
  const { t, i18n } = useTranslation();
  const { user, logout } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const onSearch = (e: FormEvent) => {
    e.preventDefault();
    const term = q.trim();
    if (!term) return;
    void navigate({ to: "/agent/search", search: { q: term } });
  };

  const switchLang = () => {
    const next = i18n.language?.startsWith("de") ? "en" : "de";
    void i18n.changeLanguage(next);
    localStorage.setItem("tiqora-lang", next);
  };

  return (
    <div className="flex min-h-screen flex-col bg-bg">
      <header className="sticky top-0 z-20 border-b border-hairline bg-surface">
        <div className="flex h-12 items-center gap-3 px-3">
          <Button
            variant="ghost"
            size="sm"
            className="lg:hidden"
            aria-label={t("admin.toggleSidebar")}
            data-testid="admin-sidebar-toggle"
            onClick={() => setSidebarOpen((o) => !o)}
          >
            ☰
          </Button>
          <Link
            to="/admin"
            className="flex shrink-0 items-center gap-2 font-display text-lg font-bold tracking-tight text-ink"
          >
            <img src="/logo.svg" alt="" width={22} height={22} className="rounded" />
            {t("app.name")}
          </Link>
          <span className="hidden shrink-0 rounded border border-hairline bg-surface-subtle px-1.5 py-0.5 text-[11px] font-medium uppercase tracking-wide text-muted sm:inline">
            {t("nav.admin")}
          </span>
          <form onSubmit={onSearch} className="mx-auto flex max-w-md flex-1">
            <input
              id="admin-search"
              data-testid="header-search"
              type="search"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder={t("search.placeholder")}
              className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink placeholder:text-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent focus:border-accent"
            />
          </form>
          <div className="flex shrink-0 items-center gap-1.5 text-sm">
            <Button variant="ghost" size="sm" onClick={toggleTheme}>
              {theme === "dark" ? "☀" : "☾"}
            </Button>
            <Button variant="ghost" size="sm" onClick={switchLang}>
              {i18n.language?.startsWith("de") ? "DE" : "EN"}
            </Button>
            {user && (
              <span
                className="hidden max-w-[10rem] truncate text-xs text-muted md:inline"
                data-testid="current-user"
                title={user.login}
              >
                {user.first_name || user.login} {user.last_name}
              </span>
            )}
            <Button
              variant="ghost"
              size="sm"
              data-testid="logout-btn"
              onClick={() => {
                void logout().then(() => navigate({ to: "/login" }));
              }}
            >
              {t("auth.logout")}
            </Button>
          </div>
        </div>
      </header>
      <div className="flex flex-1">
        <aside className="hidden w-60 shrink-0 overflow-y-auto border-r border-hairline bg-surface p-2 lg:block">
          <SidebarNav />
        </aside>
        {sidebarOpen && (
          <div className="fixed inset-0 z-30 lg:hidden">
            <button
              type="button"
              aria-label={t("common.back")}
              className="absolute inset-0 bg-black/40"
              onClick={() => setSidebarOpen(false)}
            />
            <div className="absolute inset-y-0 left-0 w-64 overflow-y-auto border-r border-hairline bg-surface p-2 shadow-xl">
              <SidebarNav onNavigate={() => setSidebarOpen(false)} />
            </div>
          </div>
        )}
        <main className={cn("min-w-0 flex-1 animate-route-in")}>{children}</main>
      </div>
    </div>
  );
}
