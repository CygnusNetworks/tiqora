import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import type { JSX } from "react";
import { vi } from "vitest";
import i18n from "@/i18n";
import { createDemoCmdb, resolveCmdb, type DemoCmdb } from "@/demo/cmdbMock";
import { api } from "@/lib/api";

export type Recorded = { method: string; path: string; body: unknown };

/** The pages talk to the demo mock through the API client: the full chain, no hand-made fixtures. */
export function serveAdminDemo(store: DemoCmdb, requests: Recorded[], override?: (method: string, path: string) => unknown) {
  vi.spyOn(api, "request").mockImplementation(async (method: string, path: string, opts?: { body?: unknown; query?: Record<string, unknown> }) => {
    requests.push({ method, path, body: opts?.body });
    if (path.endsWith("/api/v1/admin/groups")) {
      return { items: [{ id: 1, name: "users" }, { id: 2, name: "admin" }], total: 2, page: 1, page_size: 500 } as never;
    }
    const forced = override?.(method, path);
    if (forced !== undefined) return forced as never;
    const query = new URLSearchParams();
    for (const [k, v] of Object.entries(opts?.query ?? {})) if (v !== undefined && v !== null) query.append(k, String(v));
    const out = resolveCmdb(store, method, path, opts?.body, query);
    if (!out) throw new Error(`unmocked ${method} ${path}`);
    return out.body as never;
  });
}

export async function renderAdminPage(Page: () => JSX.Element, path: string) {
  const root = createRootRoute();
  const page = createRoute({ getParentRoute: () => root, path, component: Page });
  const editor = createRoute({ getParentRoute: () => root, path: "/admin/cmdb/classes/$classId", component: () => <div data-testid="class-editor-stub" /> });
  const settings = createRoute({ getParentRoute: () => root, path: "/admin/settings/$section", component: () => null });
  const router = createRouter({
    routeTree: root.addChildren([page, editor, settings]),
    history: createMemoryHistory({ initialEntries: [path] }),
  });
  await router.load();
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
        <RouterProvider router={router as any} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
  return router;
}

export { createDemoCmdb };
