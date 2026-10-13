import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { api, ApiError } from "@/lib/api";
import type { ServiceItsm } from "@/lib/itsmApi";
import { ServiceItsmDialog } from "./ServiceItsmDialog";

const service = (over: Partial<ServiceItsm> = {}): ServiceItsm => ({
  service_id: 4,
  name: "Portal",
  service_type: "Customer",
  criticality: "3 normal",
  znuny_owned: false,
  owner_user_id: 9,
  owner_name: "Alex Example",
  description: "The portal",
  portal_visible: true,
  min_minutes_between_incidents: null,
  effective_min_minutes_between_incidents: 30,
  znuny_sla_min_times: {},
  incident_state: null,
  ...over,
});

afterEach(() => vi.restoreAllMocks());

function renderDialog(onClose = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <ServiceItsmDialog serviceId={4} serviceName="Portal" onClose={onClose} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
  return onClose;
}

describe("ServiceItsmDialog", () => {
  it("suggests Znuny's service types and the matrix criticalities (free text stays)", async () => {
    vi.spyOn(api, "request").mockResolvedValue(service() as never);
    renderDialog();
    const type = await screen.findByTestId("service-itsm-type");
    const types = screen.getByTestId("service-itsm-type-suggestions");
    expect(type.getAttribute("list")).toBe(types.id);
    const typeValues = [...types.querySelectorAll("option")].map((o) => o.getAttribute("value"));
    expect(typeValues).toContain("End User Service");
    expect(typeValues).toContain("Underpinning Contract");
    expect(typeValues).toHaveLength(11);
    const crit = screen.getByTestId("service-itsm-criticality");
    const crits = screen.getByTestId("service-itsm-criticality-suggestions");
    expect(crit.getAttribute("list")).toBe(crits.id);
    expect([...crits.querySelectorAll("option")].map((o) => o.getAttribute("value"))).toEqual([
      "1 very low",
      "2 low",
      "3 normal",
      "4 high",
      "5 very high",
    ]);
  });

  it("saves the meta and keeps the owner", async () => {
    const spy = vi.spyOn(api, "request").mockImplementation((async (method: string, path: string, opts?: { body?: unknown }) => {
      if (method === "GET") return service();
      expect(path).toBe("/api/v1/admin/itsm/services/4/meta");
      expect(opts?.body).toEqual({
        service_type: "Internal",
        criticality: "3 normal",
        description: "The portal",
        portal_visible: false,
        min_minutes_between_incidents: 15,
        owner_user_id: 9,
      });
      return service();
    }) as never);
    const onClose = renderDialog();
    const type = await screen.findByTestId("service-itsm-type");
    expect(screen.queryByTestId("service-itsm-readonly-note")).toBeNull();
    fireEvent.change(type, { target: { value: "Internal" } });
    fireEvent.click(screen.getByTestId("service-itsm-portal"));
    fireEvent.change(screen.getByTestId("service-itsm-minutes"), { target: { value: "15" } });
    fireEvent.click(screen.getByTestId("service-itsm-save"));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(spy.mock.calls.some((c) => c[0] === "PUT")).toBe(true);
  });

  it("shows type and criticality read-only with a note when Znuny owns them", async () => {
    vi.spyOn(api, "request").mockResolvedValue(service({ znuny_owned: true }) as never);
    renderDialog();
    expect(await screen.findByTestId("service-itsm-readonly-note")).toBeInTheDocument();
    expect(screen.getByTestId("service-itsm-type")).toBeDisabled();
    expect(screen.getByTestId("service-itsm-criticality")).toBeDisabled();
    expect(screen.getByTestId("service-itsm-description")).not.toBeDisabled();
  });

  it("blocks a negative window and shows a refused save", async () => {
    vi.spyOn(api, "request").mockImplementation((async (method: string, path: string) => {
      if (method === "GET") return service();
      throw new ApiError(409, { detail: { code: "read_only" } }, path);
    }) as never);
    renderDialog();
    fireEvent.change(await screen.findByTestId("service-itsm-minutes"), { target: { value: "-1" } });
    expect(screen.getByTestId("service-itsm-save")).toBeDisabled();
    fireEvent.change(screen.getByTestId("service-itsm-minutes"), { target: { value: "5" } });
    fireEvent.click(screen.getByTestId("service-itsm-save"));
    expect(await screen.findByTestId("service-itsm-error")).toHaveTextContent(i18n.t("admin.itsm.service.errors.read_only"));
  });
});
