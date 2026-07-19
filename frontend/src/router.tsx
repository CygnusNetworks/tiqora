import {
  Outlet,
  createRootRoute,
  createRoute,
  createRouter,
  redirect,
} from "@tanstack/react-router";
import { LoginPage } from "@/routes/LoginPage";
import { DashboardPage } from "@/routes/agent/DashboardPage";
import { QueuesPage, type QueuesSearch } from "@/routes/agent/QueuesPage";
import { TicketZoomPage } from "@/routes/agent/TicketZoomPage";
import { SearchPage, type SearchSearch } from "@/routes/agent/SearchPage";
import { KbPage, type KbSearch } from "@/routes/agent/KbPage";
import { KbArticlePage as AgentKbArticlePage } from "@/routes/agent/KbArticlePage";
import {
  KbArticleNewPage,
  KbArticleEditPage,
} from "@/routes/agent/KbArticleEditorPage";
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
import AdminPage from "@/routes/AdminPage";

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

const adminRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/admin",
  component: AdminPage,
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
    agentTicketRoute,
    agentSearchRoute,
    agentKbRoute,
    agentKbNewRoute,
    agentKbArticleRoute,
    agentKbArticleEditRoute,
  ]),
  portalLoginRoute,
  portalLayoutRoute.addChildren([
    portalIndexRoute,
    portalNewTicketRoute,
    portalTicketRoute,
    portalKbRoute,
    portalKbArticleRoute,
  ]),
  adminRoute,
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
