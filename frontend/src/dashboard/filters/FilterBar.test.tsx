import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { api, type QueueNode } from "@/lib/api";
import { DashboardConflictError, dashboardKeys, dashboardsApi, type Dashboard } from "@/lib/dashboardsApi";
import { demoDashboard } from "@/demo/dashboardsMock";
import { FilterBar } from "./FilterBar";

const QUEUES = [
  { id: 2, name: "Support", children: [{ id: 3, name: "Support::2nd Level", children: [] }] },
  { id: 5, name: "Vertrieb", children: [] },
] as unknown as QueueNode[];

let client: QueryClient;
let patch: ReturnType<typeof vi.spyOn>;

function dash(over: Partial<Dashboard> = {}): Dashboard {
  return demoDashboard({ id: 7, name: "Mein Start", version: 4, can_edit: true, ...over });
}
function renderBar(d: Dashboard, props: { disabled?: boolean } = {}) {
  client.setQueryData(dashboardKeys.one(d.id), d);
  return render(
    <QueryClientProvider client={client}>
      <FilterBar dashboard={d} {...props} />
    </QueryClientProvider>,
  );
}

beforeAll(async () => {
  await i18n.changeLanguage("de");
});
beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  vi.spyOn(api, "listQueues").mockResolvedValue(QUEUES);
  patch = vi.spyOn(dashboardsApi, "patchFilters");
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("FilterBar", () => {
  it("owner changes the queue filter in view mode and the dashboard data refetches", async () => {
    patch.mockResolvedValue(dash({ version: 5, filters: { queue_ids: [3], time_range: null } }));
    const invalidate = vi.spyOn(client, "invalidateQueries");
    renderBar(dash());
    expect(screen.getByRole("button", { name: /Queues: Alle/ })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /Queues: Alle/ }));
    fireEvent.click(await screen.findByRole("checkbox", { name: "Support::2nd Level" }));
    fireEvent.click(screen.getByRole("button", { name: "Übernehmen" }));
    await waitFor(() => expect(patch).toHaveBeenCalledWith(7, { filters: { queue_ids: [3], time_range: null }, version: 4 }));
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: dashboardKeys.data(7) }));
    expect((client.getQueryData(dashboardKeys.one(7)) as Dashboard).version).toBe(5);
  });

  it("owner picks a time range", async () => {
    patch.mockResolvedValue(dash({ version: 5, filters: { queue_ids: [], time_range: "30d" } }));
    renderBar(dash());
    fireEvent.click(screen.getByRole("button", { name: /Zeitraum: Wie im Bericht/ }));
    fireEvent.click(await screen.findByRole("radio", { name: "Letzte 30 Tage" }));
    fireEvent.click(screen.getByRole("button", { name: "Übernehmen" }));
    await waitFor(() => expect(patch).toHaveBeenCalledWith(7, { filters: { queue_ids: [], time_range: "30d" }, version: 4 }));
  });

  it("\"Zurücksetzen\" clears the queue selection", async () => {
    patch.mockResolvedValue(dash({ version: 5 }));
    renderBar(dash({ filters: { queue_ids: [5], time_range: null } }));
    fireEvent.click(await screen.findByRole("button", { name: /Queues: Vertrieb/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Zurücksetzen" }));
    await waitFor(() => expect(patch).toHaveBeenCalledWith(7, { filters: { queue_ids: [], time_range: null }, version: 4 }));
  });

  it("read-only viewer sees active filter chips but no popover", async () => {
    renderBar(dash({ can_edit: false, filters: { queue_ids: [5], time_range: "7d" } }));
    const chip = await screen.findByText(/Queues: Vertrieb/);
    expect(chip).toBeVisible();
    expect(screen.getByText(/Zeitraum: Letzte 7 Tage/)).toBeVisible();
    expect(screen.queryByRole("button")).toBeNull();
    expect(chip.closest("[title]")).toHaveAttribute("title", "Nur wer das Dashboard bearbeiten darf, ändert die Filter.");
  });

  it("is not shown to a read-only viewer while no filter is active", () => {
    const { container } = renderBar(dash({ can_edit: false }));
    expect(container).toBeEmptyDOMElement();
  });

  it("filter bar is disabled in edit mode with the hint", () => {
    renderBar(dash(), { disabled: true });
    expect(screen.getByRole("button", { name: /Queues: Alle/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Zeitraum/ })).toBeDisabled();
    expect(screen.getByText("Filter im Ansichtsmodus ändern")).toBeVisible();
  });

  it("409 on filter change shows the conflict message and reloads", async () => {
    patch.mockRejectedValue(new DashboardConflictError("Konflikt.", 6));
    const invalidate = vi.spyOn(client, "invalidateQueries");
    renderBar(dash());
    fireEvent.click(screen.getByRole("button", { name: /Zeitraum/ }));
    fireEvent.click(await screen.findByRole("radio", { name: "Heute" }));
    fireEvent.click(screen.getByRole("button", { name: "Übernehmen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Das Dashboard wurde inzwischen geändert");
    expect(invalidate).toHaveBeenCalledWith({ queryKey: dashboardKeys.one(7) });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: dashboardKeys.data(7) });
  });
});
