import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link, useLocation, useNavigate } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";
import { useTheme } from "@/themes/theme";
import { api, type QueueNode } from "@/lib/api";
import { flattenQueues } from "@/components/agent/QueueTree";
import { Button } from "@/components/ui/Button";
import { ShortcutHelp } from "@/components/agent/ShortcutHelp";
import { NotificationBell, NotificationToaster } from "@/components/agent/NotificationBell";
import { cn } from "@/lib/cn";
import { appVersion } from "@/lib/appVersion";
import { useSSE } from "@/lib/useSSE";

/** Small "Beta" pill rendered next to the Tiqora wordmark. Replaces the old
 * full-width "not production ready" dev ribbon. */
function BetaPill() {
  const { t } = useTranslation();
  return (
    <span
      data-testid="beta-pill"
      className="rounded-full border border-accent/30 bg-accent-dim px-1.5 py-0.5 text-[9.5px] font-semibold uppercase tracking-wider text-accent"
    >
      {t("app.beta")}
    </span>
  );
}

/** Count badge shared by the nav items and, in spirit, the queue rows: shows
 * a single number (the open count) and signals "has new items" by colour
 * alone — accent-tinted pill instead of plain muted text, no "neu" chip. */
function NavCountBadge({ count, newCount }: { count: number; newCount?: number }) {
  const { t } = useTranslation();
  const hasNew = (newCount ?? 0) > 0;
  return (
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
}

function NavItem({
  to,
  search,
  label,
  count,
  newCount,
  testId,
  onNavigate,
  disabled,
  exact,
}: {
  to: string;
  search?: Record<string, unknown>;
  label: string;
  count?: number;
  newCount?: number;
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
      {count != null && <NavCountBadge count={count} newCount={newCount} />}
    </Link>
  );
}

/** One queue row in the single sidebar queue navigator: name + open-count
 * badge, linking to that queue's ticket view. Queues holding unread ("new")
 * tickets are signalled by colour alone — the badge turns accent-tinted — so
 * there is no separate "N neu" chip competing for space. Active state is
 * decided by the caller (matched against the URL's queue_id) rather than by
 * Link's path matching, since every queue row shares the same path. */
function QueueNavRow({
  node,
  active,
  onNavigate,
}: {
  node: QueueNode;
  active: boolean;
  onNavigate?: () => void;
}) {
  const { t } = useTranslation();
  const open = node.counts?.open ?? 0;
  const newCount = node.counts?.new ?? 0;
  const shortName = node.name.includes("::")
    ? (node.name.split("::").pop() ?? node.name)
    : node.name;

  return (
    <Link
      to="/agent/queues"
      search={{ queue_id: node.id, state_type: "open" }}
      onClick={onNavigate}
      data-testid={`sidebar-queue-${node.id}`}
      className={cn(
        "flex items-center gap-2 rounded-lg px-2.5 py-[7px] text-[13.5px] transition-colors duration-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
        active
          ? "bg-accent-dim font-medium text-ink shadow-[inset_2px_0_0_var(--color-accent)]"
          : "text-ink/80 hover:bg-surface-subtle",
        !node.valid && "opacity-50",
      )}
    >
      <span className="min-w-0 flex-1 truncate" title={node.name}>
        {shortName}
      </span>
      <span
        className={cn(
          "shrink-0 rounded-full px-1.5 py-0.5 font-mono text-[11px] font-semibold tabular-nums",
          newCount > 0 ? "bg-accent-dim text-accent" : "text-muted",
        )}
        title={newCount > 0 ? t("queue.newCount", { count: newCount }) : undefined}
      >
        {open}
      </span>
    </Link>
  );
}

/** The single queue navigator in the app sidebar (QueuesPage no longer
 * renders its own tree). Defaults to queues that have content (open or new
 * tickets); a search box filters by name and an "all queues" toggle reveals
 * zero-count queues. */
function QueueNavSection({ flat, onNavigate }: { flat: QueueNode[]; onNavigate?: () => void }) {
  const { t } = useTranslation();
  const location = useLocation();
  const [query, setQuery] = useState("");
  const [showAll, setShowAll] = useState(false);

  const activeQueueId = (location.search as { queue_id?: number } | undefined)?.queue_id ?? null;

  const term = query.trim().toLowerCase();
  const visible = flat.filter((q) => {
    if (term) return q.name.toLowerCase().includes(term);
    if (showAll) return true;
    return (q.counts?.open ?? 0) > 0 || (q.counts?.new ?? 0) > 0;
  });

  return (
    <div>
      <div className="flex items-center justify-between px-2.5 pb-1.5">
        <h2 className="text-[10.5px] font-semibold uppercase tracking-[0.12em] text-accent/90">
          {t("sidebar.queues")}
        </h2>
        <button
          type="button"
          data-testid="sidebar-queues-toggle-all"
          onClick={() => setShowAll((v) => !v)}
          className="text-[10px] font-medium text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
        >
          {showAll ? t("sidebar.showActiveQueues") : t("sidebar.showAllQueues")}
        </button>
      </div>
      <div className="px-0.5 pb-1.5">
        <input
          data-testid="sidebar-queue-search"
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("sidebar.queueSearch")}
          className="w-full rounded-md border border-hairline bg-surface-subtle px-2.5 py-1.5 text-[12px] text-ink placeholder:text-muted focus:border-accent focus:outline-none"
        />
      </div>
      <div className="space-y-0.5" data-testid="sidebar-queue-list">
        {visible.map((q) => (
          <QueueNavRow
            key={q.id}
            node={q}
            active={q.id === activeQueueId}
            onNavigate={onNavigate}
          />
        ))}
        {visible.length === 0 && (
          <p className="px-2.5 py-2 text-[11.5px] text-muted" data-testid="sidebar-queue-empty">
            {term ? t("sidebar.noQueueMatch") : t("queue.empty")}
          </p>
        )}
      </div>
    </div>
  );
}

/** Admin nav entry, shown only to agents who pass the same admin-capability
 * probe as RequireAdmin. Shares the ["admin","capability-probe"] query key so
 * the result is cached across the guard and this link (no extra request).
 * Renders nothing while loading or when the probe 403s — no disabled state,
 * no flicker. */
function AdminNavItem({ onNavigate }: { onNavigate?: () => void }) {
  const { t } = useTranslation();
  const probe = useQuery({
    queryKey: ["admin", "capability-probe"],
    queryFn: () => api.adminGroups.list(),
    retry: false,
    staleTime: 5 * 60 * 1000,
  });

  if (!probe.isSuccess) return null;

  return (
    <div>
      <div className="space-y-0.5">
        <NavItem
          to="/admin"
          label={t("nav.admin")}
          testId="agent-nav-admin"
          onNavigate={onNavigate}
        />
      </div>
    </div>
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
  const totalOpen = flat.reduce((sum, q) => sum + (q.counts?.open ?? 0), 0);

  // Owned-ticket counts for the "My tickets" badge — cheap COUNT(*) endpoint,
  // kept fresh on the same cadence as the queue tree.
  const myCountsQ = useQuery({
    queryKey: ["tickets", "my-counts"],
    queryFn: () => api.myTicketCounts(),
  });

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
        <BetaPill />
      </Link>

      <div className="px-0.5 pb-4">
        <SidebarSearch />
      </div>

      <nav className="flex-1 space-y-4 overflow-y-auto" data-testid="agent-sidebar-nav">
        <div>
          <h2 className="px-2.5 pb-1.5 text-[10.5px] font-semibold uppercase tracking-[0.12em] text-accent/90">
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
              count={myCountsQ.data?.open}
              newCount={myCountsQ.data?.new}
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

        <QueueNavSection flat={flat} onNavigate={onNavigate} />

        <div>
          <h2 className="px-2.5 pb-1.5 text-[10.5px] font-semibold uppercase tracking-[0.12em] text-accent/90">
            {t("sidebar.knowledge")}
          </h2>
          <div className="space-y-0.5">
            <NavItem
              to="/agent/kb"
              label={t("sidebar.knowledgeBase")}
              testId="agent-nav-kb"
              onNavigate={onNavigate}
            />
            <NavItem
              to="/agent/kb/categories"
              label={t("sidebar.kbCategories")}
              testId="agent-nav-kb-categories"
              onNavigate={onNavigate}
            />
          </div>
        </div>

        <div>
          <h2 className="px-2.5 pb-1.5 text-[10.5px] font-semibold uppercase tracking-[0.12em] text-accent/90">
            {t("sidebar.calendar")}
          </h2>
          <div className="space-y-0.5">
            <NavItem
              to="/agent/calendar"
              label={t("sidebar.calendar")}
              testId="agent-nav-calendar"
              onNavigate={onNavigate}
            />
          </div>
        </div>

        <div>
          <h2 className="px-2.5 pb-1.5 text-[10.5px] font-semibold uppercase tracking-[0.12em] text-accent/90">
            {t("sidebar.reports")}
          </h2>
          <div className="space-y-0.5">
            <NavItem
              to="/agent/stats"
              label={t("sidebar.stats")}
              testId="agent-nav-stats"
              onNavigate={onNavigate}
            />
          </div>
        </div>

        <AdminNavItem onNavigate={onNavigate} />
      </nav>

      <div className="mt-2 border-t border-hairline px-2 pt-3">
        <Link
          to="/agent/settings"
          onClick={onNavigate}
          data-testid="agent-nav-settings"
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
        <p
          className="mt-1 px-1 text-[10px] leading-none text-muted opacity-70"
          data-testid="app-version"
          title={appVersion.sha ? `commit ${appVersion.sha}` : "local dev build"}
        >
          Tiqora {appVersion.label}
        </p>
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
            <BetaPill />
          </Link>
          <div className="ml-auto flex items-center gap-1">
            <NotificationBell />
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
          <NotificationBell />
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
      <NotificationToaster />
    </div>
  );
}
