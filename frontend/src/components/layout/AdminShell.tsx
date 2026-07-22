import { useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { AccountMenu } from "@/components/agent/AccountMenu";
import { cn } from "@/lib/cn";

type NavLink = { to: string; labelKey: string; testId: string };
type NavGroup = { titleKey: string; links: NavLink[] };

const NAV_GROUPS: NavGroup[] = [
  {
    titleKey: "admin.group.usersPermissions",
    links: [
      { to: "/admin/users", labelKey: "admin.nav.users", testId: "admin-nav-users" },
      {
        to: "/admin/auth-config",
        labelKey: "admin.nav.authConfig",
        testId: "admin-nav-auth-config",
      },
      { to: "/admin/groups", labelKey: "admin.nav.groups", testId: "admin-nav-groups" },
      { to: "/admin/roles", labelKey: "admin.nav.roles", testId: "admin-nav-roles" },
      {
        to: "/admin/agent-groups",
        labelKey: "admin.nav.agentGroups",
        testId: "admin-nav-agent-groups",
      },
      {
        to: "/admin/agent-roles",
        labelKey: "admin.nav.agentRoles",
        testId: "admin-nav-agent-roles",
      },
      {
        to: "/admin/role-groups",
        labelKey: "admin.nav.roleGroups",
        testId: "admin-nav-role-groups",
      },
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
      {
        to: "/admin/customer-user-customers",
        labelKey: "admin.nav.customerUserCustomers",
        testId: "admin-nav-customer-user-customers",
      },
      {
        to: "/admin/customer-user-groups",
        labelKey: "admin.nav.customerUserGroups",
        testId: "admin-nav-customer-user-groups",
      },
    ],
  },
  {
    titleKey: "admin.group.queuesTemplates",
    links: [
      { to: "/admin/queues", labelKey: "admin.nav.queues", testId: "admin-nav-queues" },
      { to: "/admin/templates", labelKey: "admin.nav.templates", testId: "admin-nav-templates" },
      {
        to: "/admin/queue-templates",
        labelKey: "admin.nav.queueTemplates",
        testId: "admin-nav-queue-templates",
      },
      {
        to: "/admin/template-attachments",
        labelKey: "admin.nav.templateAttachments",
        testId: "admin-nav-template-attachments",
      },
      {
        to: "/admin/attachments",
        labelKey: "admin.nav.attachments",
        testId: "admin-nav-attachments",
      },
      {
        to: "/admin/auto-responses",
        labelKey: "admin.nav.autoResponses",
        testId: "admin-nav-auto-responses",
      },
      {
        to: "/admin/queue-auto-responses",
        labelKey: "admin.nav.queueAutoResponses",
        testId: "admin-nav-queue-auto-responses",
      },
      {
        to: "/admin/signatures",
        labelKey: "admin.nav.signatures",
        testId: "admin-nav-signatures",
      },
      {
        to: "/admin/salutations",
        labelKey: "admin.nav.salutations",
        testId: "admin-nav-salutations",
      },
      {
        to: "/admin/priorities",
        labelKey: "admin.nav.priorities",
        testId: "admin-nav-priorities",
      },
      { to: "/admin/states", labelKey: "admin.nav.states", testId: "admin-nav-states" },
    ],
  },
  {
    titleKey: "admin.group.email",
    links: [
      {
        to: "/admin/mail-outbound",
        labelKey: "admin.nav.mailOutbound",
        testId: "admin-nav-mail-outbound",
      },
      {
        to: "/admin/mail-log",
        labelKey: "admin.nav.mailLog",
        testId: "admin-nav-mail-log",
      },
      {
        to: "/admin/subject-config",
        labelKey: "admin.nav.subjectConfig",
        testId: "admin-nav-subject-config",
      },
    ],
  },
  {
    titleKey: "admin.group.automation",
    links: [
      { to: "/admin/acl", labelKey: "admin.nav.acl", testId: "admin-nav-acl" },
      {
        to: "/admin/generic-agent-jobs",
        labelKey: "admin.nav.genericAgentJobs",
        testId: "admin-nav-generic-agent-jobs",
      },
      {
        to: "/admin/processes",
        labelKey: "admin.nav.processes",
        testId: "admin-nav-processes",
      },
      {
        to: "/admin/postmaster-filters",
        labelKey: "admin.nav.postmasterFilters",
        testId: "admin-nav-postmaster-filters",
      },
      {
        to: "/admin/webhooks",
        labelKey: "admin.nav.webhooks",
        testId: "admin-nav-webhooks",
      },
      {
        to: "/admin/api-keys",
        labelKey: "admin.nav.apiKeys",
        testId: "admin-nav-api-keys",
      },
      {
        to: "/admin/dynamic-fields",
        labelKey: "admin.nav.dynamicFields",
        testId: "admin-nav-dynamic-fields",
      },
    ],
  },
  {
    titleKey: "admin.group.operations",
    links: [
      {
        to: "/admin/daemons",
        labelKey: "admin.nav.daemons",
        testId: "admin-nav-daemons",
      },
    ],
  },
  {
    titleKey: "admin.group.placeholderVariables",
    links: [
      {
        to: "/admin/queue-variables",
        labelKey: "admin.nav.queueVariables",
        testId: "admin-nav-queue-variables",
      },
      {
        to: "/admin/customer-fields",
        labelKey: "admin.nav.customerFields",
        testId: "admin-nav-customer-fields",
      },
    ],
  },
  {
    titleKey: "admin.group.compliance",
    links: [
      {
        to: "/admin/gdpr",
        labelKey: "admin.nav.gdpr",
        testId: "admin-nav-gdpr",
      },
    ],
  },
];

function SidebarNav({ onNavigate }: { onNavigate?: () => void }) {
  const { t } = useTranslation();
  return (
    <nav className="flex flex-col gap-3" data-testid="admin-sidebar-nav">
      {NAV_GROUPS.map((group) => (
        <div key={group.titleKey} className="nav-section-card">
          <div className="nav-section-titleband">
            <h2>{t(group.titleKey)}</h2>
          </div>
          <ul className="nav-section-body list-none space-y-0.5">
            {group.links.map((link) => (
              <li key={link.to}>
                <Link
                  to={link.to}
                  data-testid={link.testId}
                  onClick={onNavigate}
                  className="block rounded-lg px-2.5 py-[7px] text-[13.5px] text-ink transition-colors duration-100 hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
                  activeProps={{
                    className:
                      "block rounded-lg px-2.5 py-[7px] text-[13.5px] font-medium text-accent bg-accent-dim shadow-[inset_2px_0_0_var(--color-accent)]",
                  }}
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
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const onSearch = (e: FormEvent) => {
    e.preventDefault();
    const term = q.trim();
    if (!term) return;
    void navigate({ to: "/agent/search", search: { q: term } });
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
            to="/agent"
            data-testid="admin-brand-link"
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
          <div className="flex shrink-0 items-center">
            <AccountMenu logoutTestId="logout-btn" />
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
