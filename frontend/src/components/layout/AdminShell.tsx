import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { AccountMenu } from "@/components/agent/AccountMenu";
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

function SidebarSearchTrigger({ onClick }: { onClick: () => void }) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      data-testid="admin-search-trigger"
      onClick={onClick}
      className="mb-2 flex w-full items-center gap-2 rounded-lg border border-hairline bg-surface-subtle px-2.5 py-[7px] text-left text-[13.5px] text-muted transition-colors duration-100 hover:bg-surface hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
    >
      <SearchIcon className="h-4 w-4 shrink-0" />
      <span className="flex-1 truncate">{t("admin.commandPalette.placeholder")}</span>
      <kbd className="shrink-0 rounded border border-hairline bg-surface px-1 text-[10px] font-medium text-muted">
        ⌘K
      </kbd>
    </button>
  );
}

function SidebarNav({ onNavigate, onSearch }: { onNavigate?: () => void; onSearch: () => void }) {
  const { t } = useTranslation();
  return (
    <nav className="flex flex-col gap-3" data-testid="admin-sidebar-nav">
      <SidebarSearchTrigger onClick={onSearch} />
      {ADMIN_PAGE_GROUPS.map((group) => {
        const { titleKey, Icon } = GROUP_META[group];
        return (
          <div key={group} className="nav-section-card">
            <div className="nav-section-titleband flex items-center gap-1.5">
              <Icon className="h-3.5 w-3.5 shrink-0" />
              <h2>{t(titleKey)}</h2>
            </div>
            <ul className="nav-section-body list-none space-y-0.5">
              {adminPagesByGroup(group).map((page) => (
                <li key={page.slug}>
                  <Link
                    to={page.route}
                    data-testid={`admin-nav-${page.slug}`}
                    onClick={onNavigate}
                    className="flex items-center gap-2 rounded-lg px-2.5 py-[7px] text-[13.5px] text-ink transition-colors duration-100 hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
                    activeProps={{
                      className:
                        "flex items-center gap-2 rounded-lg px-2.5 py-[7px] text-[13.5px] font-medium text-accent bg-accent-dim shadow-[inset_2px_0_0_var(--color-accent)]",
                    }}
                  >
                    <Icon className="h-3.5 w-3.5 shrink-0 text-muted" />
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
  const [q, setQ] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);

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
          <SidebarNav onSearch={() => setSearchOpen(true)} />
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
              <SidebarNav
                onNavigate={() => setSidebarOpen(false)}
                onSearch={() => {
                  setSidebarOpen(false);
                  setSearchOpen(true);
                }}
              />
            </div>
          </div>
        )}
        <main className={cn("min-w-0 flex-1 animate-route-in")}>{children}</main>
      </div>
      <AdminCommandPalette open={searchOpen} onClose={() => setSearchOpen(false)} />
    </div>
  );
}
