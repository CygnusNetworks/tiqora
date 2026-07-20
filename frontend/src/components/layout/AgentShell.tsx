import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link, useLocation, useNavigate } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";
import { useTheme } from "@/themes/theme";
import { api } from "@/lib/api";
import { flattenQueues } from "@/components/agent/QueueTree";
import { Button } from "@/components/ui/Button";
import { ShortcutHelp } from "@/components/agent/ShortcutHelp";
import { cn } from "@/lib/cn";
import { useSSE } from "@/lib/useSSE";

function NavItem({
  to,
  search,
  label,
  count,
  testId,
  onNavigate,
  disabled,
  exact,
}: {
  to: string;
  search?: Record<string, unknown>;
  label: string;
  count?: number;
  testId: string;
  onNavigate?: () => void;
  disabled?: boolean;
  exact?: boolean;
}) {
  if (disabled) {
    return (
      <span
        className="flex cursor-not-allowed items-center justify-between rounded-lg px-2.5 py-[7px] text-[13.5px] text-muted/50"
        data-testid={testId}
        aria-disabled="true"
      >
        <span>{label}</span>
        {count != null && <span className="font-mono text-[11px] tabular-nums">{count}</span>}
      </span>
    );
  }
  return (
    <Link
      to={to}
      search={search}
      onClick={onNavigate}
      data-testid={testId}
      activeOptions={{ exact }}
      className="flex items-center justify-between rounded-lg px-2.5 py-[7px] text-[13.5px] text-ink/80 transition-colors duration-100 hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
      activeProps={{
        className:
          "flex items-center justify-between rounded-lg px-2.5 py-[7px] text-[13.5px] font-medium text-ink bg-accent-dim shadow-[inset_2px_0_0_var(--color-accent)]",
      }}
    >
      <span className="truncate">{label}</span>
      {count != null && (
        <span className="shrink-0 font-mono text-[11px] tabular-nums text-muted">{count}</span>
      )}
    </Link>
  );
}

function SidebarBody({ onNavigate }: { onNavigate?: () => void }) {
  const { t } = useTranslation();
  const { user } = useAuth();

  const queuesQ = useQuery({
    queryKey: ["queues"],
    queryFn: () => api.listQueues(),
  });
  const flat = flattenQueues(queuesQ.data ?? []);
  const topQueues = flat
    .slice()
    .sort((a, b) => (b.counts?.open ?? 0) - (a.counts?.open ?? 0))
    .slice(0, 6);
  const totalOpen = flat.reduce((sum, q) => sum + (q.counts?.open ?? 0), 0);

  const initials = (
    (user?.first_name?.[0] ?? user?.login?.[0] ?? "?") +
    (user?.last_name?.[0] ?? "")
  ).toUpperCase();

  return (
    <div className="flex h-full flex-col">
      <Link
        to="/agent"
        onClick={onNavigate}
        className="flex items-center gap-2.5 px-2 pb-4 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
        data-testid="agent-brand-link"
      >
        <img src="/logo.svg" alt="" width={26} height={26} className="rounded-md" />
        <span className="font-display text-[15px] font-bold tracking-tight text-ink">
          {t("app.name")}
        </span>
      </Link>

      <div className="px-0.5 pb-4">
        <SidebarSearch />
      </div>

      <nav className="flex-1 space-y-4 overflow-y-auto" data-testid="agent-sidebar-nav">
        <div>
          <h2 className="px-2.5 pb-1.5 text-[10.5px] font-semibold uppercase tracking-[0.12em] text-muted">
            {t("sidebar.workspace")}
          </h2>
          <div className="space-y-0.5">
            <NavItem
              to="/agent/queues"
              search={{ state_type: "open" }}
              label={t("sidebar.inbox")}
              count={totalOpen}
              testId="agent-nav-inbox"
              onNavigate={onNavigate}
            />
            <NavItem
              to="/agent"
              label={t("sidebar.myTickets")}
              testId="agent-nav-my-tickets"
              onNavigate={onNavigate}
              exact
            />
            <NavItem
              to="/agent"
              label={t("sidebar.watched")}
              testId="agent-nav-watched"
              disabled
            />
          </div>
        </div>

        <div>
          <h2 className="px-2.5 pb-1.5 text-[10.5px] font-semibold uppercase tracking-[0.12em] text-muted">
            {t("sidebar.queues")}
          </h2>
          <div className="space-y-0.5">
            {topQueues.map((q) => (
              <NavItem
                key={q.id}
                to="/agent/queues"
                search={{ queue_id: q.id, state_type: "open" }}
                label={q.name.includes("::") ? (q.name.split("::").pop() ?? q.name) : q.name}
                count={q.counts?.open ?? 0}
                testId={`agent-nav-queue-${q.id}`}
                onNavigate={onNavigate}
              />
            ))}
          </div>
        </div>

        <div>
          <h2 className="px-2.5 pb-1.5 text-[10.5px] font-semibold uppercase tracking-[0.12em] text-muted">
            {t("sidebar.knowledge")}
          </h2>
          <div className="space-y-0.5">
            <NavItem
              to="/agent/kb"
              label={t("sidebar.knowledgeBase")}
              testId="agent-nav-kb"
              onNavigate={onNavigate}
            />
          </div>
        </div>
      </nav>

      <div className="mt-2 border-t border-hairline px-2 pt-3">
        <Link
          to="/agent/security"
          onClick={onNavigate}
          data-testid="agent-nav-security"
          className="flex items-center gap-2.5 rounded-lg py-1 transition-colors duration-100 hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
        >
          <span
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent text-[11px] font-bold text-accent-ink"
            aria-hidden
          >
            {initials}
          </span>
          <div className="min-w-0 flex-1 leading-tight">
            <p className="truncate text-[12.5px] font-medium text-ink" data-testid="current-user">
              {user?.first_name || user?.login} {user?.last_name}
            </p>
            <p className="truncate text-[11px] text-muted">{user?.login}</p>
          </div>
        </Link>
      </div>
    </div>
  );
}

function SidebarSearch() {
  const { t } = useTranslation();
  const [q, setQ] = useState("");
  const navigate = useNavigate();

  const onSearch = (e: FormEvent) => {
    e.preventDefault();
    const term = q.trim();
    if (!term) return;
    void navigate({ to: "/agent/search", search: { q: term } });
  };

  return (
    <form onSubmit={onSearch}>
      <label htmlFor="agent-search" className="sr-only">
        {t("search.title")}
      </label>
      <div className="flex items-center justify-between gap-2 rounded-lg border border-hairline bg-surface-subtle px-3 py-2 text-muted focus-within:border-accent">
        <input
          id="agent-search"
          data-testid="header-search"
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={t("search.commandHint")}
          className="w-full min-w-0 bg-transparent text-[12.5px] text-ink placeholder:text-muted focus:outline-none"
        />
        <span className="shrink-0 rounded border border-hairline px-1.5 py-0.5 font-mono text-[10.5px]">
          ⌘K
        </span>
      </div>
    </form>
  );
}

export function AgentShell({ children }: { children: ReactNode }) {
  const { t, i18n } = useTranslation();
  const { logout } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const navigate = useNavigate();
  const location = useLocation();
  const [helpOpen, setHelpOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);

  // Realtime ticket-change + presence notifications for the whole
  // authenticated agent app — mounted once here rather than per-page.
  useSSE();

  useEffect(() => {
    setDrawerOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (e.key === "?") {
        e.preventDefault();
        setHelpOpen(true);
      }
      if (e.key === "/") {
        e.preventDefault();
        document.getElementById("agent-search")?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const switchLang = () => {
    const next = i18n.language?.startsWith("de") ? "en" : "de";
    void i18n.changeLanguage(next);
    localStorage.setItem("tiqora-lang", next);
  };

  return (
    <div className="flex min-h-screen flex-col bg-bg md:grid md:grid-cols-[216px_1fr]">
      <aside
        className="hidden shrink-0 flex-col border-r border-hairline bg-surface px-3 py-4 md:flex"
        data-testid="agent-sidebar"
      >
        <SidebarBody />
      </aside>

      {drawerOpen && (
        <div className="fixed inset-0 z-40 md:hidden">
          <button
            type="button"
            aria-label={t("common.back")}
            className="absolute inset-0 bg-black/40"
            onClick={() => setDrawerOpen(false)}
          />
          <div className="absolute inset-y-0 left-0 flex w-72 flex-col border-r border-hairline bg-surface px-3 py-4 shadow-xl">
            <SidebarBody onNavigate={() => setDrawerOpen(false)} />
          </div>
        </div>
      )}

      <div className="flex min-h-screen min-w-0 flex-1 flex-col">
        <div
          className="border-b border-hairline bg-surface-subtle px-4 py-1 text-center font-mono text-[11px] uppercase tracking-wider text-escalation"
          data-testid="dev-banner"
        >
          {t("app.devBanner")}
        </div>
        <header className="sticky top-0 z-20 flex h-11 items-center gap-2 border-b border-hairline bg-surface px-3 md:hidden">
          <Button
            variant="ghost"
            size="sm"
            aria-label={t("sidebar.toggle")}
            data-testid="agent-sidebar-toggle"
            onClick={() => setDrawerOpen((o) => !o)}
          >
            ☰
          </Button>
          <Link
            to="/agent"
            className="flex items-center gap-2 font-display text-[15px] font-bold tracking-tight text-ink"
          >
            <img src="/logo.svg" alt="" width={20} height={20} className="rounded" />
            {t("app.name")}
          </Link>
          <div className="ml-auto flex items-center gap-1">
            <Button variant="ghost" size="sm" onClick={() => setHelpOpen(true)} title="?">
              ?
            </Button>
            <Button variant="ghost" size="sm" onClick={toggleTheme}>
              {theme === "dark" ? "☀" : "☾"}
            </Button>
            <Button variant="ghost" size="sm" onClick={switchLang}>
              {i18n.language?.startsWith("de") ? "DE" : "EN"}
            </Button>
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
        </header>
        <div
          className={cn(
            "hidden items-center justify-end gap-1.5 border-b border-hairline bg-surface px-4 py-1.5 md:flex",
          )}
        >
          <Button variant="ghost" size="sm" onClick={() => setHelpOpen(true)} title="?">
            ?
          </Button>
          <Button variant="ghost" size="sm" onClick={toggleTheme}>
            {theme === "dark" ? "☀" : "☾"}
          </Button>
          <Button variant="ghost" size="sm" onClick={switchLang}>
            {i18n.language?.startsWith("de") ? "DE" : "EN"}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            data-testid="logout-btn-desktop"
            onClick={() => {
              void logout().then(() => navigate({ to: "/login" }));
            }}
          >
            {t("auth.logout")}
          </Button>
        </div>
        <main key={location.pathname} className="flex flex-1 flex-col animate-route-in">
          {children}
        </main>
      </div>
      <ShortcutHelp open={helpOpen} onClose={() => setHelpOpen(false)} />
    </div>
  );
}
