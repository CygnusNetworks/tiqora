import {
  Outlet,
  createRootRoute,
  createRoute,
  createRouter,
  redirect,
} from "@tanstack/react-router";
import { LoginPage } from "@/routes/LoginPage";
import { DashboardPage } from "@/routes/agent/DashboardPage";
import { StatsPage } from "@/routes/agent/StatsPage";
import { QueuesPage, type QueuesSearch } from "@/routes/agent/QueuesPage";
import { TicketZoomPage } from "@/routes/agent/TicketZoomPage";
import { SearchPage, type SearchSearch } from "@/routes/agent/SearchPage";
import { KbPage, type KbSearch } from "@/routes/agent/KbPage";
import { KbArticlePage as AgentKbArticlePage } from "@/routes/agent/KbArticlePage";
import { SecurityPage } from "@/routes/agent/SecurityPage";
import { SettingsPage } from "@/routes/agent/SettingsPage";
import { CalendarPage } from "@/routes/agent/CalendarPage";
import { TemplatesPage as AgentTemplatesPage } from "@/routes/agent/TemplatesPage";
import {
  NewTicketPage as AgentNewTicketPage,
  type NewTicketSearch,
} from "@/routes/agent/NewTicketPage";
import {
  KbArticleNewPage,
  KbArticleEditPage,
} from "@/routes/agent/KbArticleEditorPage";
import {
  ServiceViewPage,
  type ServiceViewSearch,
} from "@/routes/agent/ServiceViewPage";
import {
  TimeAccountingReportPage,
  type TimeAccountingSearch,
} from "@/routes/agent/TimeAccountingReportPage";
import { CustomerDetailPage } from "@/routes/agent/CustomerDetailPage";
import { AgentShell } from "@/components/layout/AgentShell";
import { PortalShell } from "@/components/layout/PortalShell";
import { RequireAuth } from "@/auth/RequireAuth";
import { RequirePortalAuth } from "@/auth/RequirePortalAuth";
import { CustomerAuthProvider } from "@/auth/CustomerAuthContext";
import { HomeRedirect } from "@/routes/HomeRedirect";
import { PortalLoginPage } from "@/routes/portal/PortalLoginPage";
import {
  TicketListPage,
  type PortalTicketListSearch,
} from "@/routes/portal/TicketListPage";
import { NewTicketPage } from "@/routes/portal/NewTicketPage";
import { TicketDetailPage } from "@/routes/portal/TicketDetailPage";
import { KbSearchPage, type PortalKbSearch } from "@/routes/portal/KbSearchPage";
import { KbArticlePage } from "@/routes/portal/KbArticlePage";
import { AdminShell } from "@/components/layout/AdminShell";
import { RequireAdmin } from "@/auth/RequireAdmin";
import { AdminHomePage } from "@/routes/admin/AdminHomePage";
import { UsersPage } from "@/routes/admin/UsersPage";
import { GroupsPage } from "@/routes/admin/GroupsPage";
import { RolesPage } from "@/routes/admin/RolesPage";
import { AgentRolesPage } from "@/routes/admin/AgentRolesPage";
import { AgentGroupsPage } from "@/routes/admin/AgentGroupsPage";
import { RoleGroupsPage } from "@/routes/admin/RoleGroupsPage";
import { CustomerUserCustomersPage } from "@/routes/admin/CustomerUserCustomersPage";
import { CustomerUserGroupsPage } from "@/routes/admin/CustomerUserGroupsPage";
import { QueuesPage as AdminQueuesPage } from "@/routes/admin/QueuesPage";
import { StatesPage } from "@/routes/admin/StatesPage";
import { PrioritiesPage } from "@/routes/admin/PrioritiesPage";
import { TypesPage } from "@/routes/admin/TypesPage";
import { ServicesPage } from "@/routes/admin/ServicesPage";
import { SlasPage } from "@/routes/admin/SlasPage";
import { SystemAddressesPage } from "@/routes/admin/SystemAddressesPage";
import { NotificationEventsPage } from "@/routes/admin/NotificationEventsPage";
import { CustomerUsersPage } from "@/routes/admin/CustomerUsersPage";
import { CustomerCompaniesPage } from "@/routes/admin/CustomerCompaniesPage";
import { TemplatesPage } from "@/routes/admin/TemplatesPage";
import { TemplateAttachmentsPage } from "@/routes/admin/TemplateAttachmentsPage";
import { AttachmentsPage } from "@/routes/admin/AttachmentsPage";
import { QueueTemplatesPage } from "@/routes/admin/QueueTemplatesPage";
import { QueueAutoResponsesPage } from "@/routes/admin/QueueAutoResponsesPage";
import { SalutationsPage } from "@/routes/admin/SalutationsPage";
import { SignaturesPage } from "@/routes/admin/SignaturesPage";
import { AutoResponsesPage } from "@/routes/admin/AutoResponsesPage";
import { DynamicFieldsPage } from "@/routes/admin/DynamicFieldsPage";
import { WebhooksPage } from "@/routes/admin/WebhooksPage";
import { MailOutboundPage } from "@/routes/admin/MailOutboundPage";
import { MailAccountsPage } from "@/routes/admin/MailAccountsPage";
import { OAuth2TokensPage } from "@/routes/admin/OAuth2TokensPage";
import { MailLogPage } from "@/routes/admin/MailLogPage";
import { SubjectConfigPage } from "@/routes/admin/SubjectConfigPage";
import { DaemonsPage } from "@/routes/admin/DaemonsPage";
import { SystemInfoPage } from "@/routes/admin/SystemInfoPage";
import { PostmasterFiltersPage } from "@/routes/admin/PostmasterFiltersPage";
import { PostmasterFilterDetailPage } from "@/routes/admin/PostmasterFilterDetailPage";
import { AclPage } from "@/routes/admin/AclPage";
import { AclDetailPage } from "@/routes/admin/AclDetailPage";
import { GenericAgentJobsPage } from "@/routes/admin/GenericAgentJobsPage";
import { GenericAgentJobDetailPage } from "@/routes/admin/GenericAgentJobDetailPage";
import { ProcessesPage } from "@/routes/admin/ProcessesPage";
import { ProcessDetailPage } from "@/routes/admin/ProcessDetailPage";
import { QueueVariablesPage } from "@/routes/admin/QueueVariablesPage";
import { CustomerFieldsPage } from "@/routes/admin/CustomerFieldsPage";
import { ApiKeysPage } from "@/routes/admin/ApiKeysPage";
import { AuthConfigPage } from "@/routes/admin/AuthConfigPage";
import { GdprPage, type GdprSearch } from "@/routes/admin/GdprPage";
import { AiSettingsPage } from "@/routes/admin/AiSettingsPage";
import { AiProvidersPage } from "@/routes/admin/AiProvidersPage";
import { AiMcpClientsPage } from "@/routes/admin/AiMcpClientsPage";
import { AiQueuePoliciesPage } from "@/routes/admin/AiQueuePoliciesPage";
import {
  AiQueuePolicyNewPage,
  AiQueuePolicyEditPage,
} from "@/routes/admin/AiQueuePolicyEditorPage";
import { AiAuditPage } from "@/routes/admin/AiAuditPage";
import { AiAclPage } from "@/routes/admin/AiAclPage";

const rootRoute = createRootRoute({
  component: () => <Outlet />,
});

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: HomeRedirect,
});

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  validateSearch: (
    s: Record<string, unknown>,
  ): { next?: string; sso_error?: string } => ({
    next: typeof s.next === "string" ? s.next : undefined,
    sso_error: typeof s.sso_error === "string" ? s.sso_error : undefined,
  }),
  component: LoginPage,
});

const agentLayoutRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/agent",
  component: () => (
    <RequireAuth>
      <AgentShell>
        <Outlet />
      </AgentShell>
    </RequireAuth>
  ),
});

const agentIndexRoute = createRoute({
  getParentRoute: () => agentLayoutRoute,
  path: "/",
  component: DashboardPage,
});

const agentQueuesRoute = createRoute({
  getParentRoute: () => agentLayoutRoute,
  path: "/queues",
  validateSearch: (s: Record<string, unknown>): QueuesSearch => {
    const num = (v: unknown) =>
      typeof v === "number"
        ? v
        : typeof v === "string" && v !== ""
          ? Number(v)
          : undefined;
    const sort = s.sort as QueuesSearch["sort"];
    const order = s.order === "asc" || s.order === "desc" ? s.order : undefined;
    const state =
      s.state_type === "new" ||
      s.state_type === "open" ||
      s.state_type === "pending" ||
      s.state_type === "closed" ||
      s.state_type === "all"
        ? s.state_type
        : undefined;
    // Customer numbers are typically all-digit strings (e.g. "10042"), and
    // the router's default search codec parses those as JS numbers rather
    // than leaving them as strings — accept both and normalize to a string
    // so `customer_id` round-trips through the URL correctly.
    const customerId =
      typeof s.customer_id === "string" && s.customer_id !== ""
        ? s.customer_id
        : typeof s.customer_id === "number"
          ? String(s.customer_id)
          : undefined;
    const bool = (v: unknown): true | undefined =>
      v === true || v === "true" || v === 1 || v === "1" ? true : undefined;
    const view =
      s.view === "locked" ||
      s.view === "mine" ||
      s.view === "responsible" ||
      s.view === "watched" ||
      s.view === "escalated" ||
      s.view === "service"
        ? s.view
        : undefined;
    return {
      queue_id: num(s.queue_id),
      state_type: state,
      customer_id: customerId,
      owner_id: num(s.owner_id),
      responsible_id: num(s.responsible_id),
      service_id: num(s.service_id),
      locked: bool(s.locked),
      watcher_user_id: num(s.watcher_user_id),
      escalated: bool(s.escalated),
      view,
      offset: num(s.offset),
      limit: num(s.limit),
      sort,
      order,
      include_archived:
        s.include_archived === true || s.include_archived === "true" ? true : undefined,
    };
  },
  component: QueuesPage,
});

// NB: register the literal "/tickets/new" route before the "$ticketId" param
// route so "new" isn't captured as a ticket id.
const agentNewTicketRoute = createRoute({
  getParentRoute: () => agentLayoutRoute,
  path: "/tickets/new",
  validateSearch: (s: Record<string, unknown>): NewTicketSearch => ({
    queue_id:
      typeof s.queue_id === "number"
        ? s.queue_id
        : typeof s.queue_id === "string" && s.queue_id !== ""
          ? Number(s.queue_id)
          : undefined,
  }),
  component: AgentNewTicketPage,
});

const agentTicketRoute = createRoute({
  getParentRoute: () => agentLayoutRoute,
  path: "/tickets/$ticketId",
  component: TicketZoomPage,
});

const agentSearchRoute = createRoute({
  getParentRoute: () => agentLayoutRoute,
  path: "/search",
  validateSearch: (s: Record<string, unknown>): SearchSearch => {
    const num = (v: unknown): number | undefined => {
      if (typeof v === "number" && Number.isFinite(v)) return v;
      if (typeof v === "string" && v !== "") {
        const n = Number(v);
        return Number.isFinite(n) ? n : undefined;
      }
      return undefined;
    };
    const numList = (v: unknown): number[] | undefined => {
      if (v === undefined || v === null || v === "") return undefined;
      const raw = Array.isArray(v) ? v : [v];
      const out = raw
        .map((x) => num(x))
        .filter((x): x is number => x !== undefined);
      return out.length ? out : undefined;
    };
    const strList = (v: unknown): string[] | undefined => {
      if (v === undefined || v === null || v === "") return undefined;
      const raw = Array.isArray(v) ? v : [v];
      const out = raw
        .filter((x): x is string => typeof x === "string" && x.length > 0)
        .map((x) => x.trim())
        .filter(Boolean);
      return out.length ? out : undefined;
    };
    const isoDate = (v: unknown): string | undefined =>
      typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v) ? v : undefined;
    const sortOrder = (v: unknown): SearchSearch["sort"] =>
      v === "created_desc" || v === "created_asc" || v === "changed_desc"
        ? v
        : undefined;

    return {
      q: typeof s.q === "string" ? s.q : undefined,
      offset: num(s.offset),
      queue_id: numList(s.queue_id),
      state_type: strList(s.state_type),
      owner_id: num(s.owner_id),
      customer_id: typeof s.customer_id === "string" ? s.customer_id : undefined,
      customer_label:
        typeof s.customer_label === "string" ? s.customer_label : undefined,
      created_from: isoDate(s.created_from),
      created_to: isoDate(s.created_to),
      sort: sortOrder(s.sort),
      include_archived:
        s.include_archived === true || s.include_archived === "true" ? true : undefined,
    };
  },
  component: SearchPage,
});

const agentKbRoute = createRoute({
  getParentRoute: () => agentLayoutRoute,
  path: "/kb",
  validateSearch: (s: Record<string, unknown>): KbSearch => {
    const num = (v: unknown) =>
      typeof v === "number"
        ? v
        : typeof v === "string" && v !== ""
          ? Number(v)
          : undefined;
    const state =
      s.state === "all" ||
      s.state === "draft" ||
      s.state === "review" ||
      s.state === "published" ||
      s.state === "archived"
        ? s.state
        : undefined;
    return {
      category_id: num(s.category_id),
      state,
      tag: typeof s.tag === "string" && s.tag !== "" ? s.tag : undefined,
      // "articles" is the default and stays out of the URL.
      tab: s.tab === "categories" ? "categories" : undefined,
      new: s.new === true || s.new === 1 || s.new === "1" ? true : undefined,
    };
  },
  component: KbPage,
});

const agentKbNewRoute = createRoute({
  getParentRoute: () => agentLayoutRoute,
  path: "/kb/new",
  component: KbArticleNewPage,
});

const agentKbCategoriesRoute = createRoute({
  getParentRoute: () => agentLayoutRoute,
  path: "/kb/categories",
  // Categories are now the "Kategorien" tab of the KB page. Keep the old URL
  // (and its ?new deep link) working by redirecting to /agent/kb?tab=categories.
  validateSearch: (s: Record<string, unknown>): { new?: boolean } =>
    s.new === true || s.new === 1 || s.new === "1" ? { new: true } : {},
  beforeLoad: ({ search }) => {
    throw redirect({
      to: "/agent/kb",
      search: { tab: "categories", ...(search.new ? { new: true } : {}) },
    });
  },
});

const agentKbArticleRoute = createRoute({
  getParentRoute: () => agentLayoutRoute,
  path: "/kb/$articleId",
  component: AgentKbArticlePage,
});

const agentKbArticleEditRoute = createRoute({
  getParentRoute: () => agentLayoutRoute,
  path: "/kb/$articleId/edit",
  component: KbArticleEditPage,
});

const agentSecurityRoute = createRoute({
  getParentRoute: () => agentLayoutRoute,
  path: "/security",
  component: SecurityPage,
});

const agentSettingsRoute = createRoute({
  getParentRoute: () => agentLayoutRoute,
  path: "/settings",
  component: SettingsPage,
});

const agentStatsRoute = createRoute({
  getParentRoute: () => agentLayoutRoute,
  path: "/stats",
  component: StatsPage,
});

const agentCalendarRoute = createRoute({
  getParentRoute: () => agentLayoutRoute,
  path: "/calendar",
  component: CalendarPage,
});

const agentTemplatesRoute = createRoute({
  getParentRoute: () => agentLayoutRoute,
  path: "/templates",
  component: AgentTemplatesPage,
});

const agentServicesRoute = createRoute({
  getParentRoute: () => agentLayoutRoute,
  path: "/services",
  validateSearch: (s: Record<string, unknown>): ServiceViewSearch => {
    const num = (v: unknown) =>
      typeof v === "number"
        ? v
        : typeof v === "string" && v !== ""
          ? Number(v)
          : undefined;
    const state =
      s.state_type === "new" ||
      s.state_type === "open" ||
      s.state_type === "pending" ||
      s.state_type === "closed" ||
      s.state_type === "all"
        ? s.state_type
        : undefined;
    return {
      service_id: num(s.service_id),
      state_type: state,
      offset: num(s.offset),
    };
  },
  component: ServiceViewPage,
});

const agentTimeAccountingRoute = createRoute({
  getParentRoute: () => agentLayoutRoute,
  path: "/time-accounting",
  validateSearch: (s: Record<string, unknown>): TimeAccountingSearch => {
    const num = (v: unknown) =>
      typeof v === "number"
        ? v
        : typeof v === "string" && v !== ""
          ? Number(v)
          : undefined;
    const isoDate = (v: unknown): string | undefined =>
      typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v) ? v : undefined;
    return {
      create_by: num(s.create_by),
      ticket_id: num(s.ticket_id),
      created_from: isoDate(s.created_from),
      created_to: isoDate(s.created_to),
      offset: num(s.offset),
    };
  },
  component: TimeAccountingReportPage,
});

const agentCustomerRoute = createRoute({
  getParentRoute: () => agentLayoutRoute,
  path: "/customers/$login",
  component: CustomerDetailPage,
});

// /portal/login: mounts its own CustomerAuthProvider (a separate session from
// the agent AuthProvider) — not gated, since it must render for a
// not-yet-authenticated customer.
const portalLoginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/portal/login",
  validateSearch: (s: Record<string, unknown>): { next?: string } => ({
    next: typeof s.next === "string" ? s.next : undefined,
  }),
  component: () => (
    <CustomerAuthProvider>
      <PortalLoginPage />
    </CustomerAuthProvider>
  ),
});

// /portal: gated portal shell — CustomerAuthProvider + RequirePortalAuth.
const portalLayoutRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/portal",
  component: () => (
    <CustomerAuthProvider>
      <RequirePortalAuth>
        <PortalShell>
          <Outlet />
        </PortalShell>
      </RequirePortalAuth>
    </CustomerAuthProvider>
  ),
});

const portalIndexRoute = createRoute({
  getParentRoute: () => portalLayoutRoute,
  path: "/",
  validateSearch: (s: Record<string, unknown>): PortalTicketListSearch => {
    const state =
      s.state_type === "open" ||
      s.state_type === "pending" ||
      s.state_type === "closed" ||
      s.state_type === "all"
        ? s.state_type
        : undefined;
    return { state_type: state };
  },
  component: TicketListPage,
});

const portalNewTicketRoute = createRoute({
  getParentRoute: () => portalLayoutRoute,
  path: "/tickets/new",
  component: NewTicketPage,
});

const portalTicketRoute = createRoute({
  getParentRoute: () => portalLayoutRoute,
  path: "/tickets/$ticketId",
  component: TicketDetailPage,
});

const portalKbRoute = createRoute({
  getParentRoute: () => portalLayoutRoute,
  path: "/kb",
  validateSearch: (s: Record<string, unknown>): PortalKbSearch => ({
    q: typeof s.q === "string" ? s.q : undefined,
  }),
  component: KbSearchPage,
});

const portalKbArticleRoute = createRoute({
  getParentRoute: () => portalLayoutRoute,
  path: "/kb/$slug",
  component: KbArticlePage,
});

// /admin: agent session (RequireAuth) + is_admin from /me (RequireAdmin)
// gated shell with a grouped left sidebar nav (see AdminShell).
const adminLayoutRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/admin",
  component: () => (
    <RequireAdmin>
      <AdminShell>
        <Outlet />
      </AdminShell>
    </RequireAdmin>
  ),
});

const adminIndexRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/",
  component: AdminHomePage,
});

const adminUsersRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/users",
  component: UsersPage,
});

const adminAuthConfigRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/auth-config",
  component: AuthConfigPage,
});

const adminGroupsRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/groups",
  component: GroupsPage,
});

const adminRolesRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/roles",
  component: RolesPage,
});

const adminAgentRolesRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/agent-roles",
  component: AgentRolesPage,
});

const adminAgentGroupsRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/agent-groups",
  component: AgentGroupsPage,
});

const adminRoleGroupsRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/role-groups",
  component: RoleGroupsPage,
});

const adminQueuesRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/queues",
  component: AdminQueuesPage,
});

const adminStatesRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/states",
  component: StatesPage,
});

const adminPrioritiesRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/priorities",
  component: PrioritiesPage,
});

const adminTypesRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/types",
  component: TypesPage,
});

const adminServicesRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/services",
  component: ServicesPage,
});

const adminSlasRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/slas",
  component: SlasPage,
});

const adminSystemAddressesRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/system-addresses",
  component: SystemAddressesPage,
});

const adminNotificationEventsRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/notification-events",
  component: NotificationEventsPage,
});

const adminCustomerUsersRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/customer-users",
  component: CustomerUsersPage,
});

const adminCustomerCompaniesRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/customer-companies",
  component: CustomerCompaniesPage,
});

const adminCustomerUserCustomersRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/customer-user-customers",
  component: CustomerUserCustomersPage,
});

const adminCustomerUserGroupsRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/customer-user-groups",
  component: CustomerUserGroupsPage,
});

const adminTemplatesRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/templates",
  component: TemplatesPage,
});

const adminTemplateAttachmentsRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/template-attachments",
  component: TemplateAttachmentsPage,
});

const adminAttachmentsRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/attachments",
  component: AttachmentsPage,
});

const adminQueueTemplatesRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/queue-templates",
  component: QueueTemplatesPage,
});

const adminQueueAutoResponsesRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/queue-auto-responses",
  component: QueueAutoResponsesPage,
});

const adminSalutationsRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/salutations",
  component: SalutationsPage,
});

const adminSignaturesRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/signatures",
  component: SignaturesPage,
});

const adminAutoResponsesRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/auto-responses",
  component: AutoResponsesPage,
});

const adminDynamicFieldsRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/dynamic-fields",
  component: DynamicFieldsPage,
});

const adminWebhooksRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/webhooks",
  component: WebhooksPage,
});

const adminApiKeysRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/api-keys",
  component: ApiKeysPage,
});

const adminMailOutboundRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/mail-outbound",
  component: MailOutboundPage,
});

const adminMailAccountsRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/mail-accounts",
  component: MailAccountsPage,
});

const adminOAuth2TokensRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/oauth2-tokens",
  component: OAuth2TokensPage,
});

const adminMailLogRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/mail-log",
  component: MailLogPage,
});

const adminSubjectConfigRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/subject-config",
  component: SubjectConfigPage,
});

const adminPostmasterFiltersRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/postmaster-filters",
  component: PostmasterFiltersPage,
});

const adminPostmasterFilterDetailRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/postmaster-filters/$name",
  component: PostmasterFilterDetailPage,
});

const adminAclRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/acl",
  component: AclPage,
});

const adminAclDetailRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/acl/$aclId",
  component: AclDetailPage,
});

const adminGenericAgentJobsRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/generic-agent-jobs",
  component: GenericAgentJobsPage,
});

const adminGenericAgentJobDetailRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/generic-agent-jobs/$jobName",
  component: GenericAgentJobDetailPage,
});

const adminProcessesRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/processes",
  component: ProcessesPage,
});

const adminQueueVariablesRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/queue-variables",
  component: QueueVariablesPage,
});

const adminCustomerFieldsRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/customer-fields",
  component: CustomerFieldsPage,
});

const adminGdprRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/gdpr",
  validateSearch: (s: Record<string, unknown>): GdprSearch => ({
    logins: typeof s.logins === "string" ? s.logins : undefined,
    tab: s.tab === "jobs" || s.tab === "run" ? s.tab : undefined,
  }),
  component: GdprPage,
});

const adminDaemonsRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/daemons",
  component: DaemonsPage,
});

const adminSystemRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/system",
  component: SystemInfoPage,
});

const adminProcessDetailRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/processes/$processEntityId",
  component: ProcessDetailPage,
});

const adminAiRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/ai",
  component: AiSettingsPage,
});

const adminAiProvidersRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/ai/providers",
  component: AiProvidersPage,
});

const adminAiMcpRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/ai/mcp",
  component: AiMcpClientsPage,
});

const adminAiQueuesRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/ai/queues",
  component: AiQueuePoliciesPage,
});

const adminAiQueueNewRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/ai/queues/new",
  component: AiQueuePolicyNewPage,
});

const adminAiQueueEditRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/ai/queues/$policyId",
  component: AiQueuePolicyEditPage,
});

const adminAiAuditRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/ai/audit",
  component: AiAuditPage,
});

const adminAiAclRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/ai/acl",
  component: AiAclPage,
});

const catchAllRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "$",
  beforeLoad: () => {
    throw redirect({ to: "/" });
  },
});

const routeTree = rootRoute.addChildren([
  indexRoute,
  loginRoute,
  agentLayoutRoute.addChildren([
    agentIndexRoute,
    agentQueuesRoute,
    agentNewTicketRoute,
    agentTicketRoute,
    agentSearchRoute,
    agentKbRoute,
    agentKbNewRoute,
    agentKbCategoriesRoute,
    agentKbArticleRoute,
    agentKbArticleEditRoute,
    agentSecurityRoute,
    agentSettingsRoute,
    agentStatsRoute,
    agentCalendarRoute,
    agentTemplatesRoute,
    agentServicesRoute,
    agentTimeAccountingRoute,
    agentCustomerRoute,
  ]),
  portalLoginRoute,
  portalLayoutRoute.addChildren([
    portalIndexRoute,
    portalNewTicketRoute,
    portalTicketRoute,
    portalKbRoute,
    portalKbArticleRoute,
  ]),
  adminLayoutRoute.addChildren([
    adminIndexRoute,
    adminUsersRoute,
    adminAuthConfigRoute,
    adminGroupsRoute,
    adminRolesRoute,
    adminAgentRolesRoute,
    adminAgentGroupsRoute,
    adminRoleGroupsRoute,
    adminQueuesRoute,
    adminStatesRoute,
    adminPrioritiesRoute,
    adminTypesRoute,
    adminServicesRoute,
    adminSlasRoute,
    adminSystemAddressesRoute,
    adminNotificationEventsRoute,
    adminCustomerUsersRoute,
    adminCustomerCompaniesRoute,
    adminCustomerUserCustomersRoute,
    adminCustomerUserGroupsRoute,
    adminTemplatesRoute,
    adminTemplateAttachmentsRoute,
    adminAttachmentsRoute,
    adminQueueTemplatesRoute,
    adminQueueAutoResponsesRoute,
    adminSalutationsRoute,
    adminSignaturesRoute,
    adminAutoResponsesRoute,
    adminDynamicFieldsRoute,
    adminWebhooksRoute,
    adminApiKeysRoute,
    adminMailOutboundRoute,
    adminMailAccountsRoute,
    adminOAuth2TokensRoute,
    adminMailLogRoute,
    adminSubjectConfigRoute,
    adminPostmasterFiltersRoute,
    adminPostmasterFilterDetailRoute,
    adminAclRoute,
    adminAclDetailRoute,
    adminGenericAgentJobsRoute,
    adminGenericAgentJobDetailRoute,
    adminProcessesRoute,
    adminProcessDetailRoute,
    adminQueueVariablesRoute,
    adminCustomerFieldsRoute,
    adminGdprRoute,
    adminDaemonsRoute,
    adminSystemRoute,
    adminAiRoute,
    adminAiProvidersRoute,
    adminAiMcpRoute,
    adminAiQueuesRoute,
    adminAiQueueNewRoute,
    adminAiQueueEditRoute,
    adminAiAuditRoute,
    adminAiAclRoute,
  ]),
  catchAllRoute,
]);

// Vite serves the demo under a project sub-path (e.g. "/tiqora/demo/").
// TanStack Router needs the same prefix or every <Navigate to="/agent" /> and
// Link would jump to the host root (https://…github.io/agent) and 404.
const routerBasepath = (import.meta.env.BASE_URL || "/").replace(/\/+$/, "") || "/";

export const router = createRouter({
  routeTree,
  defaultPreload: "intent",
  basepath: routerBasepath,
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
