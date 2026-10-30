/** Shared fixtures of the change administration tests: the demo mock behind the API client and a memory router. */
import type { JSX } from "react";
import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { vi } from "vitest";
import i18n from "@/i18n";
import { resolveApprovalsData } from "@/demo/approvalsMock";
import { resolveChangeReply } from "@/demo/changeMock";
import { api } from "@/lib/api";

export type Recorded = { method: string; path: string; body: unknown };
type Override = (method: string, path: string, body: unknown) => unknown;

const REFERENCE: Record<string, unknown> = {
  "/api/v1/reference/agents": [
    { id: 2, login: "alex.turner", full_name: "Alex Turner" },
    { id: 3, login: "erika.beispiel", full_name: "Erika Beispiel" },
  ],
  "/api/v1/reference/share-groups": [{ id: 7, name: "Betriebsleitung" }],
  "/api/v1/reference/roles": [{ id: 4, name: "Change Manager" }],
  "/api/v1/reference/customers": [{ login: "kunde@example.org", email: "kunde@example.org", customer_id: "example", full_name: "Kim Kunde" }],
  "/api/v1/reference/services": [
    { id: 1, name: "Kundenportal" },
    { id: 2, name: "E-Mail" },
  ],
  "/api/v1/reference/priorities": [
    { id: 3, name: "3 normal" },
    { id: 4, name: "4 high" },
  ],
  "/api/v1/queues": [
    { id: 1, name: "Raw", children: [] },
    { id: 5, name: "Betrieb", children: [] },
  ],
  "/api/v1/approvals/lists": [{ id: 1, name: "CAB Netzwerk", member_count: 3 }],
};

/**
 * Every call goes through the demo change mock (Mode B) or the reference data above; `override`
 * answers first (throw an `ApiError` there to fail a call). Calls are recorded with their body.
 */
export function serveChangeAdmin(requests: Recorded[] = [], override?: Override) {
  return vi.spyOn(api, "request").mockImplementation((async (method: string, path: string, opts?: { body?: unknown }) => {
    requests.push({ method, path, body: opts?.body });
    const forced = override?.(method, path, opts?.body);
    if (forced !== undefined) return forced;
    if (path in REFERENCE) return structuredClone(REFERENCE[path]);
    if (path === "/api/v1/approvals/resolvers") return resolveApprovalsData(path, "GET");
    const reply = resolveChangeReply(path, method, "", "tiqora", opts?.body);
    if (!reply) throw new Error(`unmocked ${method} ${path}`);
    return structuredClone(reply.body ?? null);
  }) as never);
}

/** Renders `Page` on `routePath` while the memory history sits on `url`; stub routes for the links the pages follow. */
export async function renderChangeAdmin(Page: () => JSX.Element, routePath: string, url = routePath) {
  window.scrollTo = vi.fn();
  const root = createRootRoute();
  const stubs = [
    ["/admin/itsm/change", "change-admin-stub"],
    ["/admin/itsm/change/templates/$templateId", "template-editor-stub"],
    ["/admin/settings/$section", "settings-stub"],
  ].filter(([path]) => path !== routePath);
  const routes = [
    createRoute({ getParentRoute: () => root, path: routePath, component: Page }),
    ...stubs.map(([path, testId]) =>
      createRoute({ getParentRoute: () => root, path, component: () => <div data-testid={testId} /> }),
    ),
  ];
  const router = createRouter({ routeTree: root.addChildren(routes), history: createMemoryHistory({ initialEntries: [url] }) });
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
  return { router, queryClient };
}

/** The body of the first recorded call `method path`. */
export function bodyOf(requests: Recorded[], method: string, path: string): Record<string, unknown> | undefined {
  return requests.find((r) => r.method === method && r.path === path)?.body as Record<string, unknown> | undefined;
}

export async function useGerman() {
  await i18n.changeLanguage("de");
}
