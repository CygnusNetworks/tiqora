import type { ReactElement } from "react";
import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { vi } from "vitest";
import i18n from "@/i18n";
import { api } from "@/lib/api";
import type { ApprovalDetail } from "@/lib/approvalsApi";
import { resolveApprovalsData } from "@/demo/approvalsMock";

type Handler = (init: { body?: unknown; query?: unknown } | undefined) => unknown;

/** Demo approval 501 (CAB, two stages), 502 (decided) or 503 (overdue, escalated) as a fresh copy. */
export function demoDetail(id: 501 | 502 | 503): ApprovalDetail {
  return structuredClone(resolveApprovalsData(`/api/v1/approvals/${id}`, "GET") as ApprovalDetail);
}

/** Answers the reference data the pickers load; `extra` handlers are keyed `"METHOD /path"` and win. */
export function mockApi(extra: Record<string, Handler> = {}) {
  return vi.spyOn(api, "request").mockImplementation((async (method: string, path: string, init?: never) => {
    const handler = extra[`${method} ${path}`];
    if (handler) return handler(init);
    if (path === "/api/v1/reference/agents") {
      return [
        { id: 2, login: "alex.turner", full_name: "Alex Turner" },
        { id: 3, login: "erika.muster", full_name: "Erika Muster" },
      ];
    }
    if (path === "/api/v1/reference/share-groups") return [{ id: 7, name: "Betriebsleitung" }];
    if (path === "/api/v1/reference/roles") return [{ id: 4, name: "Change Manager" }];
    if (path === "/api/v1/reference/customers") {
      return [{ login: "kunde@example.org", email: "kunde@example.org", customer_id: "example", full_name: "Kim Kunde" }];
    }
    if (path === "/api/v1/admin/approvals/subject-types") {
      return [
        { key: "itsm.change", title_key: "approvals.subject.itsm_change" },
        { key: "ticket", title_key: "approvals.subject.ticket" },
      ];
    }
    if (path === "/api/v1/approvals/lists") return [{ id: 1, name: "CAB Netzwerk", member_count: 3 }];
    if (path === "/api/v1/approvals/resolvers") {
      return resolveApprovalsData("/api/v1/approvals/resolvers", "GET");
    }
    throw new Error(`unmocked ${method} ${path}`);
  }) as never);
}

export async function useGerman() {
  await i18n.changeLanguage("de");
}

export function renderApproval(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={client}>{ui}</QueryClientProvider>
    </I18nextProvider>,
  );
}
