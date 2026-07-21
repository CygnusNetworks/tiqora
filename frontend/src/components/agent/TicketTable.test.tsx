import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import {
  RouterProvider,
  createRootRoute,
  createRoute,
  createRouter,
  createMemoryHistory,
} from "@tanstack/react-router";
import i18n from "@/i18n";
import type { TicketListItem } from "@/lib/api";
import { TicketTable } from "./TicketTable";

function makeItem(overrides: Partial<TicketListItem> = {}): TicketListItem {
  return {
    id: 11,
    tn: "20240601000011",
    title: "Help",
    queue_id: 1,
    queue_name: "Support",
    state_id: 1,
    state: "new",
    state_type: "new",
    priority_id: 3,
    priority: "3 normal",
    lock_id: 1,
    lock: "unlock",
    owner_id: 1,
    create_time: "2024-06-01T12:00:00",
    change_time: "2024-06-01T12:00:00",
    age_seconds: 3600,
    escalation_time: 0,
    escalation_response_time: 0,
    escalation_update_time: 0,
    escalation_solution_time: 0,
    until_time: 0,
    ...overrides,
  } as TicketListItem;
}

async function renderTable(items: TicketListItem[]) {
  const ui = (
    <I18nextProvider i18n={i18n}>
      <TicketTable
        items={items}
        total={items.length}
        offset={0}
        limit={25}
        sort="age"
        order="desc"
        onSortChange={vi.fn()}
        onPageChange={vi.fn()}
      />
    </I18nextProvider>
  );
  const rootRoute = createRootRoute({ component: () => ui });
  const ticketRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/agent/tickets/$ticketId",
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([ticketRoute]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  await router.load();
  return render(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    <RouterProvider router={router as any} />,
  );
}

describe("TicketTable state display", () => {
  it("shows a soft-chip for new tickets with the localised label", async () => {
    await renderTable([makeItem()]);
    const chip = await screen.findByTestId("ticket-state-chip-11");
    expect(chip).toHaveTextContent("New");
    expect(chip).toHaveAttribute("data-kind", "state");
    expect(chip).toHaveStyle({ color: "var(--color-state-new)" });
    // Old special-case NEU badge is gone.
    expect(screen.queryByTestId("ticket-new-badge-11")).toBeNull();
  });

  it("shows a soft-chip for non-new states with the same chip markup", async () => {
    await renderTable([
      makeItem({
        id: 12,
        tn: "20240601000012",
        state: "closed successful",
        state_type: "closed",
      }),
    ]);
    const chip = await screen.findByTestId("ticket-state-chip-12");
    expect(chip).toHaveTextContent("Closed successful");
    expect(chip).toHaveAttribute("data-kind", "state");
    expect(chip).toHaveStyle({ color: "var(--color-state-closed)" });
  });

  it("shows a soft-chip for priority without the numeric rank", async () => {
    await renderTable([makeItem({ priority: "5 very high", priority_id: 5 })]);
    const chip = await screen.findByTestId("ticket-priority-chip-11");
    expect(chip).toHaveTextContent("very high");
    expect(chip).not.toHaveTextContent("5 very");
    expect(chip).toHaveAttribute("data-kind", "priority");
    expect(chip).toHaveStyle({ color: "var(--color-prio-5)" });
  });
});
