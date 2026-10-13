import { useEffect, useState, type ReactNode } from "react";
import { Link, useLocation } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, type QueueNode } from "@/lib/api";
import { cmdbNavVisible, itsmApi, itsmKeys } from "@/lib/itsmApi";
import { logoUrl } from "@/lib/assets";
import { useAuth } from "@/auth/AuthContext";
import { flattenQueues } from "@/components/agent/QueueTree";
import { isSystemQueue } from "@/lib/systemQueues";
import { Button } from "@/components/ui/Button";
import { ShortcutHelp } from "@/components/agent/ShortcutHelp";
import { NotificationBell, NotificationToaster } from "@/components/agent/NotificationBell";
import { CommandSearch } from "@/components/agent/CommandSearch";
import { NewTicketButton } from "@/components/agent/NewTicketButton";
import { ConnectionStatus } from "@/components/agent/ConnectionStatus";
import { AccountMenu } from "@/components/agent/AccountMenu";
import { OnlineAgentsPopover } from "@/components/agent/OnlineAgentsPopover";
import { TimeZoneHint } from "@/components/agent/TimeZoneHint";
import { SavedSearchNav } from "@/components/searches/SavedSearchNav";
import { ChatInboxButton } from "@/components/agent/webchat/ChatInboxButton";
import { ChatRingProvider } from "@/components/agent/webchat/ChatRingContext";
import { effectiveTimeZone } from "@/lib/timeZone";
import {
  AddressBookIcon,
  BookIcon,
  CalendarIcon,
  ChartIcon,
  ChevronDownIcon,
  ClockIcon,
  EyeIcon,
  HelpIcon,
  InboxIcon,
  MailIcon,
  PlusIcon,
  SearchIcon,
  ServerIcon,
  ShieldIcon,
  UserIcon,
} from "@/components/ui/icons";
import { cn } from "@/lib/cn";
import { appVersion } from "@/lib/appVersion";
import { SSEProvider } from "@/lib/useSSE";
import { CallPopup } from "@/components/agent/phone/CallPopup";
import { NavItem } from "./NavItem";
import { PluginProvider } from "@/plugins/PluginProvider";
import { PluginNavItems, PluginSafeModeNote } from "@/plugins/slots";
import { useNavGroupHasItems } from "@/plugins/useNavGroupHasItems";

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

const SIDEBAR_COLLAPSED_GROUPS_KEY = "tiqora.sidebar.collapsedGroups";
const SIDEBAR_SHOW_SYSTEM_QUEUES_KEY = "tiqora.sidebar.showSystemQueues";

function readShowSystemQueues(): boolean {
  try {
    return window.localStorage.getItem(SIDEBAR_SHOW_SYSTEM_QUEUES_KEY) === "1";
  } catch {
    return false;
  }
}

function readCollapsedGroups(): Record<string, boolean> {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(SIDEBAR_COLLAPSED_GROUPS_KEY);
    return raw ? (JSON.parse(raw) as Record<string, boolean>) : {};
  } catch {
    return {};
  }
}

/** Flat, frameless sidebar section: an uppercase label row that expands or
 * collapses the group's entries (per-group state persisted in
 * localStorage). Replaces the old boxed "nav-section-card" look for the
 * agent sidebar — AdminShell still uses that card style. */
function NavGroup({
  id,
  title,
  collapsed,
  onToggle,
  action,
  children,
}: {
  id: string;
  title: string;
  collapsed: boolean;
  onToggle: () => void;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div>
      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={!collapsed}
          data-testid={`sidebar-group-${id}-toggle`}
          className="flex flex-1 items-center gap-1.5 rounded py-1 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
        >
          <ChevronDownIcon
            className={cn(
              "h-3 w-3 shrink-0 text-muted transition-transform duration-100",
              collapsed && "-rotate-90",
            )}
          />
          <h2 className="flex-1 truncate text-[10px] font-semibold uppercase tracking-[0.09em] text-muted">
            {title}
          </h2>
        </button>
        {action}
      </div>
      {!collapsed && <div className="space-y-0.5 py-1">{children}</div>}
    </div>
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
      search={{ queue_id: node.id }}
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
 * zero-count queues. Znuny's plumbing queues (Postmaster, Raw, Junk) stay
 * hidden behind a remembered per-browser toggle unless searched for or open. */
function QueueNavSection({
  flat,
  onNavigate,
  collapsed,
  onToggle,
}: {
  flat: QueueNode[];
  onNavigate?: () => void;
  collapsed: boolean;
  onToggle: () => void;
}) {
  const { t } = useTranslation();
  const location = useLocation();
  const [query, setQuery] = useState("");
  const [showAll, setShowAll] = useState(false);
  const [showSystem, setShowSystem] = useState(readShowSystemQueues);
  const toggleSystem = () => {
    const next = !showSystem;
    setShowSystem(next);
    try {
      window.localStorage.setItem(SIDEBAR_SHOW_SYSTEM_QUEUES_KEY, next ? "1" : "0");
    } catch {
      // best-effort persistence only
    }
  };

  const activeQueueId = (location.search as { queue_id?: number } | undefined)?.queue_id ?? null;

  const term = query.trim().toLowerCase();
  const listed = flat.filter((q) => {
    if (term) return q.name.toLowerCase().includes(term);
    if (showAll) return true;
    return (q.counts?.open ?? 0) > 0 || (q.counts?.new ?? 0) > 0;
  });
  const hiddenSystem = term
    ? []
    : listed.filter((q) => isSystemQueue(q) && q.id !== activeQueueId);
  const visible = showSystem ? listed : listed.filter((q) => !hiddenSystem.includes(q));

  return (
    <NavGroup
      id="queues"
      title={t("sidebar.queues")}
      collapsed={collapsed}
      onToggle={onToggle}
      action={
        <button
          type="button"
          data-testid="sidebar-queues-toggle-all"
          onClick={() => setShowAll((v) => !v)}
          className="shrink-0 text-[10px] font-medium normal-case tracking-normal text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
        >
          {showAll ? t("sidebar.showActiveQueues") : t("sidebar.showAllQueues")}
        </button>
      }
    >
      <div className="pb-1.5">
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
          <QueueNavRow key={q.id} node={q} active={q.id === activeQueueId} onNavigate={onNavigate} />
        ))}
        {visible.length === 0 && (
          <p className="px-2.5 py-2 text-[11.5px] text-muted" data-testid="sidebar-queue-empty">
            {term ? t("sidebar.noQueueMatch") : t("queue.empty")}
          </p>
        )}
        {hiddenSystem.length > 0 && (
          <button
            type="button"
            data-testid="sidebar-queues-toggle-system"
            onClick={toggleSystem}
            className="w-full rounded-lg px-2.5 py-1 text-left text-[11.5px] text-muted hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
          >
            {showSystem
              ? t("sidebar.hideSystemQueues")
              : t("sidebar.showSystemQueues", { count: hiddenSystem.length })}
          </button>
        )}
      </div>
    </NavGroup>
  );
}

function SidebarBody({ onNavigate }: { onNavigate?: () => void }) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const hasPluginGroup = useNavGroupHasItems("plugins");

  const queuesQ = useQuery({
    queryKey: ["queues"],
    queryFn: () => api.listQueues(),
  });
  // Not gated: the status route answers in every mode. A failure just hides the entry.
  const itsmStatusQ = useQuery({
    queryKey: itsmKeys.status(),
    queryFn: ({ signal }) => itsmApi.status(signal),
    staleTime: 5 * 60_000,
    retry: false,
  });
  const flat = flattenQueues(queuesQ.data ?? []);
  const totalOpen = flat.reduce((sum, q) => sum + (q.counts?.open ?? 0), 0);

  // Owned-ticket counts for the "My tickets" badge — cheap COUNT(*) endpoint,
  // kept fresh on the same cadence as the queue tree.
  const myCountsQ = useQuery({
    queryKey: ["tickets", "my-counts"],
    queryFn: () => api.myTicketCounts(),
  });
  // SLA-escalated count for the inbox's red pill — the dashboard's KPI
  // endpoint, shared cache entry with the dashboard tile.
  const summaryQ = useQuery({
    queryKey: ["tickets", "dashboard-summary"],
    queryFn: () => api.dashboardSummary(),
  });

  // The ticket-list entries share one route; which one is current is
  // decided by the preset `view` / `queue_id` in the URL.
  const location = useLocation();
  const onQueues = location.pathname === "/agent/queues";
  const listSearch = (location.search ?? {}) as { view?: string; queue_id?: number };

  const [collapsedGroups, setCollapsedGroups] =
    useState<Record<string, boolean>>(readCollapsedGroups);
  useEffect(() => {
    try {
      window.localStorage.setItem(SIDEBAR_COLLAPSED_GROUPS_KEY, JSON.stringify(collapsedGroups));
    } catch {
      // best-effort persistence only
    }
  }, [collapsedGroups]);
  const toggleGroup = (id: string) =>
    setCollapsedGroups((prev) => ({ ...prev, [id]: !prev[id] }));

  return (
    <div className="flex h-full flex-col">
      <Link
        to="/agent"
        onClick={onNavigate}
        className="flex items-center gap-2.5 px-2 pb-4 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
        data-testid="agent-brand-link"
      >
        <img src={logoUrl} alt="" width={26} height={26} className="rounded-md" />
        <span className="font-display text-[15px] font-bold tracking-tight text-ink">
          {t("app.name")}
        </span>
        <BetaPill />
      </Link>

      <nav className="flex-1 space-y-2 overflow-y-auto" data-testid="agent-sidebar-nav">
        <NavGroup
          id="workspace"
          title={t("sidebar.workspace")}
          collapsed={!!collapsedGroups.workspace}
          onToggle={() => toggleGroup("workspace")}
        >
          <NavItem
            to="/agent/search"
            label={t("search.title")}
            icon={<SearchIcon />}
            testId="agent-nav-search"
            onNavigate={onNavigate}
          />
          <NavItem
            to="/agent/queues"
            label={t("sidebar.inbox")}
            icon={<InboxIcon />}
            count={totalOpen}
            alertCount={summaryQ.data?.escalated}
            testId="agent-nav-inbox"
            onNavigate={onNavigate}
            active={onQueues && !listSearch.view && listSearch.queue_id == null}
          />
          <NavItem
            to="/agent/queues"
            search={{ owner_id: user?.id, view: "mine" }}
            label={t("sidebar.myTickets")}
            icon={<UserIcon />}
            count={myCountsQ.data?.open}
            newCount={myCountsQ.data?.new}
            testId="agent-nav-my-tickets"
            onNavigate={onNavigate}
            active={onQueues && listSearch.view === "mine"}
          />
          <NavItem
            to="/agent/queues"
            search={{ responsible_id: user?.id, view: "responsible" }}
            label={t("sidebar.responsible")}
            icon={<ShieldIcon />}
            testId="agent-nav-responsible"
            onNavigate={onNavigate}
            disabled={!user?.id}
            active={onQueues && listSearch.view === "responsible"}
          />
          <NavItem
            to="/agent/queues"
            search={{ watcher_user_id: user?.id, view: "watched" }}
            label={t("sidebar.watched")}
            icon={<EyeIcon />}
            testId="agent-nav-watched"
            onNavigate={onNavigate}
            disabled={!user?.id}
            active={onQueues && listSearch.view === "watched"}
          />
          {user?.can_use_customer_directory && (
            <NavItem
              to="/agent/customers"
              label={t("sidebar.customers")}
              icon={<AddressBookIcon />}
              testId="agent-nav-customers"
              onNavigate={onNavigate}
            />
          )}
          <NavItem
            to="/agent/services"
            label={t("sidebar.services")}
            icon={<ServerIcon />}
            testId="agent-nav-services"
            onNavigate={onNavigate}
          />
          <PluginNavItems group="workspace" onNavigate={onNavigate} />
        </NavGroup>

        <NavGroup
          id="searches"
          title={t("sidebar.searches")}
          collapsed={!!collapsedGroups.searches}
          onToggle={() => toggleGroup("searches")}
          action={
            <Link
              to="/agent/searches/new"
              onClick={onNavigate}
              aria-label={t("savedSearches.newAria")}
              title={t("savedSearches.new")}
              data-testid="sidebar-searches-new"
              className="flex h-5 w-5 shrink-0 items-center justify-center rounded text-muted hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
            >
              <PlusIcon className="h-3.5 w-3.5" />
            </Link>
          }
        >
          <SavedSearchNav onNavigate={onNavigate} />
        </NavGroup>

        <QueueNavSection
          flat={flat}
          onNavigate={onNavigate}
          collapsed={!!collapsedGroups.queues}
          onToggle={() => toggleGroup("queues")}
        />

        <NavGroup
          id="knowledge"
          title={t("sidebar.knowledge")}
          collapsed={!!collapsedGroups.knowledge}
          onToggle={() => toggleGroup("knowledge")}
        >
          {/* Knowledge base and its categories now live on one tabbed page; the
              category management is the "Kategorien" tab of /agent/kb, so the
              sidebar needs a single entry here. */}
          <NavItem
            to="/agent/kb"
            label={t("sidebar.knowledgeBase")}
            icon={<BookIcon />}
            testId="agent-nav-kb"
            onNavigate={onNavigate}
          />
          {user?.can_edit_templates && (
            <NavItem
              to="/agent/templates"
              label={t("sidebar.templates")}
              icon={<MailIcon />}
              testId="agent-nav-templates"
              onNavigate={onNavigate}
            />
          )}
          <PluginNavItems group="knowledge" onNavigate={onNavigate} />
        </NavGroup>

        {/* Calendar and reports were each a full section wrapping a single
            link; merged into one "Insights" group. */}
        <NavGroup
          id="insights"
          title={t("sidebar.insights")}
          collapsed={!!collapsedGroups.insights}
          onToggle={() => toggleGroup("insights")}
        >
          <NavItem
            to="/agent/calendar"
            label={t("sidebar.calendar")}
            icon={<CalendarIcon />}
            testId="agent-nav-calendar"
            onNavigate={onNavigate}
          />
          {user?.can_view_reports && (
            <NavItem
              to="/agent/reports"
              label={t("sidebar.stats")}
              icon={<ChartIcon />}
              testId="agent-nav-stats"
              onNavigate={onNavigate}
            />
          )}
          <NavItem
            to="/agent/time-accounting"
            label={t("sidebar.timeAccounting")}
            icon={<ClockIcon />}
            testId="agent-nav-time-accounting"
            onNavigate={onNavigate}
          />
          {cmdbNavVisible(itsmStatusQ.data) && (
            <NavItem
              to="/agent/itsm/cis"
              label={t("sidebar.cmdb")}
              icon={<ServerIcon />}
              testId="agent-nav-cmdb"
              onNavigate={onNavigate}
            />
          )}
          <PluginNavItems group="insights" onNavigate={onNavigate} />
        </NavGroup>

        {hasPluginGroup && (
          <NavGroup
            id="plugins"
            title={t("sidebar.plugins")}
            collapsed={!!collapsedGroups.plugins}
            onToggle={() => toggleGroup("plugins")}
          >
            <PluginNavItems group="plugins" onNavigate={onNavigate} />
          </NavGroup>
        )}
        <PluginSafeModeNote />
      </nav>

      {/* Version/git-sha footer — kept after removing the redundant bottom-left user card. */}
      <div className="mt-2 border-t border-hairline px-2 pt-2.5 pb-0.5">
        <p
          className="px-1 text-[10px] leading-none text-muted opacity-70"
          data-testid="app-version"
          title={appVersion.sha ? `commit ${appVersion.sha}` : "local dev build"}
        >
          Tiqora {appVersion.label}
        </p>
      </div>
    </div>
  );
}

/** Ghost icon button used for the header's help toggle — 32px square, matching
 * the notification bell so the cluster reads as one row of controls. */
function HeaderIconButton({
  onClick,
  label,
  children,
  testId,
}: {
  onClick: () => void;
  label: string;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <button
      type="button"
      data-testid={testId}
      aria-label={label}
      title={label}
      onClick={onClick}
      className="flex h-8 w-8 items-center justify-center rounded-lg text-ink/70 transition-colors duration-100 hover:bg-surface-subtle hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
    >
      {children}
    </button>
  );
}

/** The cohesive top-right control cluster shared by the desktop and mobile
 * agent headers: search · new · connection · online · bell · help │ account. */
function HeaderControls({
  onHelp,
  logoutTestId,
  searchFill = false,
}: {
  onHelp: () => void;
  logoutTestId: string;
  /** Let the search bar grow into the available header width (desktop). */
  searchFill?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <div className={cn("flex items-center gap-1.5", searchFill && "w-full")}>
      {searchFill ? (
        <div className="min-w-0 flex-1">
          <CommandSearch fill />
        </div>
      ) : (
        <CommandSearch />
      )}
      <NewTicketButton />
      <div className="mx-0.5 flex items-center gap-0.5">
        <ConnectionStatus />
        <OnlineAgentsPopover />
        <ChatInboxButton variant={searchFill ? "desktop" : "mobile"} />
        <NotificationBell />
        <HeaderIconButton onClick={onHelp} label={t("shortcuts.title")} testId="header-help">
          <HelpIcon className="text-[18px]" />
        </HeaderIconButton>
      </div>
      <div className="mx-1 h-6 w-px bg-hairline" aria-hidden />
      <AccountMenu logoutTestId={logoutTestId} />
    </div>
  );
}

export function AgentShell({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const location = useLocation();
  const [helpOpen, setHelpOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  // Part of the page key: a changed display zone re-renders every date.
  const timeZone = effectiveTimeZone(useAuth().user?.time_zone);

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
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Realtime ticket-change + presence notifications for the whole authenticated
  // agent app — the stream opens once here (SSEProvider) and its connection
  // state feeds the header's ConnectionStatus dot.
  return (
    <PluginProvider>
    <SSEProvider>
      <ChatRingProvider>
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
          <header className="sticky top-0 z-20 flex h-12 items-center gap-2 border-b border-hairline bg-surface px-3 md:hidden">
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
              <img src={logoUrl} alt="" width={20} height={20} className="rounded" />
              {t("app.name")}
              <BetaPill />
            </Link>
            <div className="ml-auto">
              <HeaderControls onHelp={() => setHelpOpen(true)} logoutTestId="logout-btn" />
            </div>
          </header>
          <div className="hidden items-center border-b border-hairline bg-surface px-4 py-1.5 md:flex">
            <HeaderControls
              onHelp={() => setHelpOpen(true)}
              logoutTestId="logout-btn-desktop"
              searchFill
            />
          </div>
          <TimeZoneHint />
          <main
            key={`${location.pathname}|${timeZone}`}
            className="flex flex-1 flex-col animate-route-in"
          >
            {children}
          </main>
        </div>
        <ShortcutHelp open={helpOpen} onClose={() => setHelpOpen(false)} />
        <NotificationToaster />
        <CallPopup />
      </div>
      </ChatRingProvider>
    </SSEProvider>
    </PluginProvider>
  );
}
