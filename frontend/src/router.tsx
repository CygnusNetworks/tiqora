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
import { AgentShell } from "@/components/layout/AgentShell";
import { RequireAuth } from "@/auth/RequireAuth";
import { HomeRedirect } from "@/routes/HomeRedirect";
import PortalPage from "@/routes/PortalPage";
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

const portalRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/portal",
  component: PortalPage,
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
  ]),
  portalRoute,
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
