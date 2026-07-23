import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate, useRouterState } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { AccountMenu } from "@/components/agent/AccountMenu";
import { AdminCommandPalette } from "@/components/admin/AdminCommandPalette";
import {
  SearchIcon,
  UsersIcon,
  TicketIcon,
  MailIcon,
  BoltIcon,
  ServerIcon,
  SparkIcon,
  HomeIcon,
  ChevronLeftIcon,
} from "@/components/ui/icons";
import {
  ADMIN_PAGE_GROUPS,
  ADMIN_PAGES,
  adminPagesByGroup,
  type AdminPageGroup,
} from "@/lib/adminSearch";
import { cn } from "@/lib/cn";

const GROUP_META: Record<
  AdminPageGroup,
  { titleKey: string; Icon: typeof UsersIcon }
> = {
  access: { titleKey: "admin.group.access", Icon: UsersIcon },
  tickets: { titleKey: "admin.group.tickets", Icon: TicketIcon },
  communication: { titleKey: "admin.group.communication", Icon: MailIcon },
  ai: { titleKey: "admin.group.ai", Icon: SparkIcon },
  automation: { titleKey: "admin.group.automation", Icon: BoltIcon },
  system: { titleKey: "admin.group.system", Icon: ServerIcon },
};

const NAV_COLLAPSED_KEY = "tiqora.admin.nav.collapsed";

/** Group of the admin page whose route is the longest prefix of *pathname*
 * — `/admin/ai/queues/5` resolves to the `ai-queues` page, not `ai`. */
function groupForPath(pathname: string): AdminPageGroup | null {
  let best: { group: AdminPageGroup; len: number } | null = null;
  for (const page of ADMIN_PAGES) {
    if (pathname === page.route || pathname.startsWith(`${page.route}/`)) {
      if (!best || page.route.length > best.len) {
        best = { group: page.group, len: page.route.length };
      }
    }
  }
  return best?.group ?? null;
}

function SidebarSearchTrigger({
  onClick,
  compact,
}: {
  onClick: () => void;
  compact?: boolean;
}) {
  const { t } = useTranslation();
  if (compact) {
    return (
      <button
        type="button"
        data-testid="admin-search-trigger"
        onClick={onClick}
        title={`${t("admin.commandPalette.placeholder")} (⌘K)`}
        className="flex h-9 w-9 items-center justify-center rounded-lg text-muted transition-colors duration-100 hover:bg-surface-subtle hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
      >
        <SearchIcon className="h-4 w-4" />
      </button>
    );
  }
  return (
    <button
      type="button"
      data-testid="admin-search-trigger"
      onClick={onClick}
      className="mb-2 flex w-full items-center gap-2 rounded-lg border border-hairline bg-surface-subtle px-2.5 py-[7px] text-left text-[13.5px] text-muted transition-colors duration-100 hover:bg-surface hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
    >
      <SearchIcon className="h-4 w-4 shrink-0" />
      <span className="flex-1 truncate">
        {t("admin.commandPalette.placeholder")}
      </span>
      <kbd className="shrink-0 rounded border border-hairline bg-surface px-1 text-[10px] font-medium text-muted">
        ⌘K
      </kbd>
    </button>
  );
}

/** Variante 1 desktop nav: a slim icon rail (home + one icon per group) and
 * a context column that lists only the ACTIVE group's pages. The column can
 * be collapsed entirely (persisted) for full-width content. */
function RailNav({
  activeGroup,
  onSelectGroup,
  onSearch,
}: {
  activeGroup: AdminPageGroup;
  onSelectGroup: (g: AdminPageGroup) => void;
  onSearch: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div
      className="flex w-[52px] shrink-0 flex-col items-center gap-1 border-r border-hairline bg-surface py-2"
      data-testid="admin-nav-rail"
    >
      <SidebarSearchTrigger onClick={onSearch} compact />
      <Link
        to="/admin"
        data-testid="admin-rail-home"
        title={t("nav.home")}
        activeOptions={{ exact: true }}
        className="flex h-9 w-9 items-center justify-center rounded-lg text-muted transition-colors duration-100 hover:bg-surface-subtle hover:text-ink"
        activeProps={{
          className:
            "flex h-9 w-9 items-center justify-center rounded-lg bg-accent-dim text-accent",
        }}
      >
        <HomeIcon className="h-4 w-4" />
      </Link>
      <div className="my-1 h-px w-7 bg-hairline" aria-hidden />
      {ADMIN_PAGE_GROUPS.map((group) => {
        const { titleKey, Icon } = GROUP_META[group];
        const active = group === activeGroup;
        return (
          <button
            key={group}
            type="button"
            data-testid={`admin-rail-${group}`}
            title={t(titleKey)}
            aria-pressed={active}
            onClick={() => onSelectGroup(group)}
            className={cn(
              "flex h-9 w-9 items-center justify-center rounded-lg transition-colors duration-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
              active
                ? "bg-accent-dim text-accent"
                : "text-muted hover:bg-surface-subtle hover:text-ink",
            )}
          >
            <Icon className="h-4 w-4" />
          </button>
        );
      })}
    </div>
  );
}

function ContextColumn({
  group,
  onCollapse,
  onNavigate,
}: {
  group: AdminPageGroup;
  onCollapse: () => void;
  onNavigate?: () => void;
}) {
  const { t } = useTranslation();
  const { titleKey } = GROUP_META[group];
  return (
    <div
      className="flex w-48 shrink-0 flex-col border-r border-hairline bg-surface p-2"
      data-testid="admin-nav-context"
    >
      <div className="mb-1 flex items-center justify-between gap-1 px-1.5 pt-1">
        <h2 className="text-[10.5px] font-semibold uppercase tracking-wide text-muted">
          {t(titleKey)}
        </h2>
        <button
          type="button"
          data-testid="admin-nav-collapse"
          title={t("admin.nav.collapse")}
          onClick={onCollapse}
          className="rounded p-0.5 text-muted hover:bg-surface-subtle hover:text-ink"
        >
          <ChevronLeftIcon className="h-3.5 w-3.5" />
        </button>
      </div>
      <ul className="list-none space-y-0.5 overflow-y-auto">
        {adminPagesByGroup(group).map((page) => (
          <li key={page.slug}>
            <Link
              to={page.route}
              data-testid={`admin-nav-${page.slug}`}
              onClick={onNavigate}
              className="flex items-center rounded-lg px-2.5 py-[7px] text-[13.5px] text-ink transition-colors duration-100 hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
              activeProps={{
                className:
                  "flex items-center rounded-lg px-2.5 py-[7px] text-[13.5px] font-medium text-accent bg-accent-dim shadow-[inset_2px_0_0_var(--color-accent)]",
              }}
            >
              <span className="truncate">{t(page.nameKey)}</span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Mobile keeps the full grouped list (all groups, all pages) in the
 * overlay — the rail pattern doesn't translate to a sheet. Styled like the
 * desktop redesign's frameless look (flat group headers, no boxed cards). */
function MobileNav({
  onNavigate,
  onSearch,
}: {
  onNavigate?: () => void;
  onSearch: () => void;
}) {
  const { t } = useTranslation();
  return (
    <nav className="flex flex-col gap-4" data-testid="admin-sidebar-nav">
      <SidebarSearchTrigger onClick={onSearch} />
      {ADMIN_PAGE_GROUPS.map((group) => {
        const { titleKey, Icon } = GROUP_META[group];
        return (
          <div key={group}>
            <div className="mb-1 flex items-center gap-1.5 px-2.5 text-[11px] font-semibold uppercase tracking-wide text-muted">
              <Icon className="h-3.5 w-3.5 shrink-0" />
              <h2>{t(titleKey)}</h2>
            </div>
            <ul className="list-none space-y-0.5">
              {adminPagesByGroup(group).map((page) => (
                <li key={page.slug}>
                  <Link
                    to={page.route}
                    data-testid={`admin-nav-mobile-${page.slug}`}
                    onClick={onNavigate}
                    className="flex items-center gap-2 rounded-lg px-2.5 py-[7px] text-[13.5px] text-ink transition-colors duration-100 hover:bg-surface-subtle"
                    activeProps={{
                      className:
                        "flex items-center gap-2 rounded-lg px-2.5 py-[7px] text-[13.5px] font-medium text-accent bg-accent-dim shadow-[inset_2px_0_0_var(--color-accent)]",
                    }}
                  >
                    <span className="truncate">{t(page.nameKey)}</span>
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </nav>
  );
}

export function AdminShell({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const pathname = useRouterState({ select: (st) => st.location.pathname });
  const [q, setQ] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(NAV_COLLAPSED_KEY) === "1",
  );
  // The rail's active group: follows the route, but a rail click may preview
  // another group without navigating.
  const routeGroup = groupForPath(pathname);
  const [pinnedGroup, setPinnedGroup] = useState<AdminPageGroup | null>(null);
  const activeGroup = pinnedGroup ?? routeGroup ?? "access";

  // Route changes win over a previewed group.
  useEffect(() => {
    setPinnedGroup(null);
  }, [pathname]);

  const toggleCollapsed = () => {
    setCollapsed((c) => {
      localStorage.setItem(NAV_COLLAPSED_KEY, c ? "0" : "1");
      return !c;
    });
  };

  const onSelectGroup = (g: AdminPageGroup) => {
    setPinnedGroup(g);
    if (collapsed) toggleCollapsed();
  };

  const onSearch = (e: FormEvent) => {
    e.preventDefault();
    const term = q.trim();
    if (!term) return;
    void navigate({ to: "/agent/search", search: { q: term } });
  };

  // ⌘K / Ctrl+K opens the admin command palette from anywhere in the shell.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSearchOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

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
            <img
              src="/logo.svg"
              alt=""
              width={22}
              height={22}
              className="rounded"
            />
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
        <aside className="hidden lg:flex" data-testid="admin-sidebar">
          <RailNav
            activeGroup={activeGroup}
            onSelectGroup={onSelectGroup}
            onSearch={() => setSearchOpen(true)}
          />
          {!collapsed && (
            <ContextColumn group={activeGroup} onCollapse={toggleCollapsed} />
          )}
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
              <MobileNav
                onNavigate={() => setSidebarOpen(false)}
                onSearch={() => {
                  setSidebarOpen(false);
                  setSearchOpen(true);
                }}
              />
            </div>
          </div>
        )}
        <main className={cn("min-w-0 flex-1 animate-route-in")}>
          {children}
        </main>
      </div>
      <AdminCommandPalette
        open={searchOpen}
        onClose={() => setSearchOpen(false)}
      />
    </div>
  );
}
