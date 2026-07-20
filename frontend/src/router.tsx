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
import {
  NewTicketPage as AgentNewTicketPage,
  type NewTicketSearch,
} from "@/routes/agent/NewTicketPage";
import {
  KbArticleNewPage,
  KbArticleEditPage,
} from "@/routes/agent/KbArticleEditorPage";
import { KbCategoriesPage } from "@/routes/agent/KbCategoriesPage";
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
import { QueuesPage as AdminQueuesPage } from "@/routes/admin/QueuesPage";
import { StatesPage } from "@/routes/admin/StatesPage";
import { PrioritiesPage } from "@/routes/admin/PrioritiesPage";
import { CustomerUsersPage } from "@/routes/admin/CustomerUsersPage";
import { CustomerCompaniesPage } from "@/routes/admin/CustomerCompaniesPage";
import { TemplatesPage } from "@/routes/admin/TemplatesPage";
import { SalutationsPage } from "@/routes/admin/SalutationsPage";
import { SignaturesPage } from "@/routes/admin/SignaturesPage";
import { AutoResponsesPage } from "@/routes/admin/AutoResponsesPage";
import { DynamicFieldsPage } from "@/routes/admin/DynamicFieldsPage";
import { WebhooksPage } from "@/routes/admin/WebhooksPage";
import { PostmasterFiltersPage } from "@/routes/admin/PostmasterFiltersPage";
import { PostmasterFilterDetailPage } from "@/routes/admin/PostmasterFilterDetailPage";
import { AclPage } from "@/routes/admin/AclPage";
import { AclDetailPage } from "@/routes/admin/AclDetailPage";
import { GenericAgentJobsPage } from "@/routes/admin/GenericAgentJobsPage";
import { GenericAgentJobDetailPage } from "@/routes/admin/GenericAgentJobDetailPage";
import { ProcessesPage } from "@/routes/admin/ProcessesPage";
import { ProcessDetailPage } from "@/routes/admin/ProcessDetailPage";

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
  validateSearch: (s: Record<string, unknown>): { next?: string } => ({
    next: typeof s.next === "string" ? s.next : undefined,
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
    return {
      queue_id: num(s.queue_id),
      state_type: state,
      offset: num(s.offset),
      limit: num(s.limit),
      sort,
      order,
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
  validateSearch: (s: Record<string, unknown>): SearchSearch => ({
    q: typeof s.q === "string" ? s.q : undefined,
    offset:
      typeof s.offset === "number"
        ? s.offset
        : typeof s.offset === "string"
          ? Number(s.offset)
          : undefined,
  }),
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
    return { category_id: num(s.category_id), state };
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
  component: KbCategoriesPage,
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

// /admin: agent session (RequireAuth) + admin-capability probe (RequireAdmin)
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

const adminTemplatesRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/templates",
  component: TemplatesPage,
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

const adminProcessDetailRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/processes/$processEntityId",
  component: ProcessDetailPage,
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
    adminGroupsRoute,
    adminRolesRoute,
    adminAgentRolesRoute,
    adminAgentGroupsRoute,
    adminRoleGroupsRoute,
    adminQueuesRoute,
    adminStatesRoute,
    adminPrioritiesRoute,
    adminCustomerUsersRoute,
    adminCustomerCompaniesRoute,
    adminCustomerUserCustomersRoute,
    adminTemplatesRoute,
    adminSalutationsRoute,
    adminSignaturesRoute,
    adminAutoResponsesRoute,
    adminDynamicFieldsRoute,
    adminWebhooksRoute,
    adminPostmasterFiltersRoute,
    adminPostmasterFilterDetailRoute,
    adminAclRoute,
    adminAclDetailRoute,
    adminGenericAgentJobsRoute,
    adminGenericAgentJobDetailRoute,
    adminProcessesRoute,
    adminProcessDetailRoute,
  ]),
  catchAllRoute,
]);

export const router = createRouter({
  routeTree,
  defaultPreload: "intent",
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
