import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { api } from "@/lib/api";
import { ServicesPage } from "./ServicesPage";

const SERVICE = { id: 4, name: "Portal", valid_id: 1, comments: null, sla_ids: [], change_time: "2026-07-01T00:00:00Z" };

beforeEach(() => {
  vi.spyOn(api, "request").mockImplementation((async (method: string, path: string) => {
    if (path === "/api/v1/admin/services" && method === "GET") return { items: [SERVICE], total: 1, page: 1, page_size: 25 };
    if (path === "/api/v1/admin/itsm/services/4/meta") {
      return {
        service_id: 4, name: "Portal", service_type: "Customer", criticality: "3 normal", znuny_owned: true, owner_user_id: null,
        owner_name: null, description: null, portal_visible: true, min_minutes_between_incidents: null,
        effective_min_minutes_between_incidents: 30, znuny_sla_min_times: {}, incident_state: null,
      };
    }
    throw new Error(`unexpected ${method} ${path}`);
  }) as never);
});
afterEach(() => vi.restoreAllMocks());

describe("ServicesPage ITSM section", () => {
  it("opens the ITSM data of a service from the row menu", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <I18nextProvider i18n={i18n}>
          <ServicesPage />
        </I18nextProvider>
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByTestId("admin-row-menu-trigger-4"));
    fireEvent.click(await screen.findByTestId("admin-service-itsm-4"));
    expect(await screen.findByTestId("service-itsm-type")).toHaveValue("Customer");
    expect(screen.getByTestId("service-itsm-type")).toBeDisabled();
  });
});
