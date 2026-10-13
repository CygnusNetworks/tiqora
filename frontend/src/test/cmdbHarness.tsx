import { vi } from "vitest";
import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import type { ReactNode } from "react";
import i18n from "@/i18n";
import { resolveCmdb, type DemoCmdb } from "@/demo/cmdbMock";
import { api, ApiError } from "@/lib/api";

export type Seen = { method: string; path: string; query: URLSearchParams; body: unknown };

/**
 * Answers every `api.request` of a CMDB component from the demo store (the same resolver the demo
 * and the e2e mock use). `extra` can answer a path first (ticket search and the like).
 */
export function serveCmdb(
  store: DemoCmdb,
  seen: Seen[],
  extra?: (method: string, path: string, query: URLSearchParams) => unknown | undefined,
) {
  return vi.spyOn(api, "request").mockImplementation(async (method: string, path: string, opts?: { body?: unknown; query?: Record<string, unknown> }) => {
    const query = new URLSearchParams();
    for (const [k, v] of Object.entries(opts?.query ?? {})) {
      if (v === undefined || v === null) continue;
      query.append(k, String(v));
    }
    seen.push({ method, path, query, body: opts?.body });
    const own = extra?.(method, path, query);
    if (own !== undefined) return own as never;
    const out = resolveCmdb(store, method, path, opts?.body, query);
    if (!out) throw new Error(`unmocked ${method} ${path}`);
    if (out.status === 404) throw new ApiError(404, "Nicht gefunden.", path);
    return out.body as never;
  });
}

/** Renders `ui` inside query client, i18n and a memory router (links need one). */
export async function renderWithRouter(ui: ReactNode) {
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo; // the router restores scroll; jsdom has none
  const rootRoute = createRootRoute({ component: () => <>{ui}</> });
  const ci = createRoute({ getParentRoute: () => rootRoute, path: "/agent/cmdb/$ciId", component: () => <div data-testid="ci-route" /> });
  const ticket = createRoute({ getParentRoute: () => rootRoute, path: "/agent/tickets/$ticketId", component: () => null });
  const index = createRoute({ getParentRoute: () => rootRoute, path: "/", component: () => null });
  const router = createRouter({ routeTree: rootRoute.addChildren([index, ci, ticket]), history: createMemoryHistory({ initialEntries: ["/"] }) });
  await router.load();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
        <RouterProvider router={router as any} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
  return { router, qc };
}
