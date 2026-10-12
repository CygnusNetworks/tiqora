import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import i18n from "@/i18n";
import { validateScheduleNewSearch } from "@/routes/agent/reportScheduleSearch";

type Page = () => React.JSX.Element | null;

/** Test helper: the three schedule routes (and a stand-in list) under /agent/reports in a memory router. */
export function renderSchedulePages(
  path: string,
  pages: { list?: Page; editor?: Page },
) {
  const root = createRootRoute({ component: () => <Outlet /> });
  const agent = createRoute({ getParentRoute: () => root, path: "/agent", component: () => <Outlet /> });
  const overview = createRoute({ getParentRoute: () => agent, path: "/reports", component: () => <p data-testid="reports-overview">Berichte</p> });
  const list = createRoute({ getParentRoute: () => agent, path: "/reports/schedules", component: pages.list ?? (() => <p data-testid="schedules-stand-in">Zeitpläne</p>) });
  const created = createRoute({
    getParentRoute: () => agent,
    path: "/reports/schedules/new",
    validateSearch: validateScheduleNewSearch,
    component: pages.editor ?? (() => null),
  });
  const edit = createRoute({ getParentRoute: () => agent, path: "/reports/schedules/$scheduleId", component: pages.editor ?? (() => null) });
  const router = createRouter({
    routeTree: root.addChildren([agent.addChildren([overview, list, created, edit])]),
    history: createMemoryHistory({ initialEntries: [path] }),
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <RouterProvider router={router} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
  return { router, qc };
}
