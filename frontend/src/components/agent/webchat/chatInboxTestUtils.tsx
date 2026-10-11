import { vi } from "vitest";
import { render } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import i18n from "@/i18n";

export const item = (over: Record<string, unknown> = {}) => ({
  session_id: "s1",
  ticket_id: 116,
  tn: "2026070116",
  title: "Chat",
  status: "waiting",
  visitor_name: "Erika Musterfrau",
  topic_label: "Support",
  waiting_since: new Date(Date.now() - 65_000).toISOString(),
  accepted_at: null,
  ...over,
});

export const topics = [{ ref: "1:support", label: "Support", queue_id: 1, config_name: "Website" }];
export const agentState = { available: false, max_concurrent: 3, active_count: 0, topics: [] as string[] };

/** Routes the `api.request` mock by method + path onto per-endpoint handlers. */
export function routeRequests(
  request: ReturnType<typeof vi.fn>,
  h: {
    inbox?: () => unknown;
    state?: () => unknown;
    topics?: () => unknown;
    accept?: (sid: string) => unknown;
    put?: (body: unknown) => unknown;
  },
) {
  request.mockReset().mockImplementation((method: string, path: string, opts?: { body?: unknown }) => {
    if (path.endsWith("/accept")) return Promise.resolve().then(() => h.accept?.(path.split("/")[5]));
    if (path.endsWith("/agent-state")) {
      return method === "PUT"
        ? Promise.resolve().then(() => h.put?.(opts?.body))
        : Promise.resolve(h.state ? h.state() : agentState);
    }
    if (path.endsWith("/topics")) return Promise.resolve(h.topics ? h.topics() : topics);
    if (path.endsWith("/inbox")) return Promise.resolve(h.inbox ? h.inbox() : { waiting: [], mine: [] });
    return Promise.resolve({});
  });
}

export function renderWithProviders(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>{ui}</I18nextProvider>
    </QueryClientProvider>,
  );
}
