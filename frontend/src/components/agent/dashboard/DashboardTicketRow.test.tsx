import { describe, it, expect } from "vitest";
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
import { DashboardTicketRow } from "./DashboardTicketRow";

const ticket: TicketListItem = {
  id: 42,
  tn: "20240601000042",
  title: "Printer on fire",
  queue_id: 3,
  queue_name: "Support::Level 2",
  state_id: 4,
  state: "open",
  state_type: "open",
  priority_id: 5,
  priority: "5 very high",
  lock_id: 1,
  lock: "unlock",
  owner_id: 1,
  create_time: "2024-06-01T12:00:00",
  change_time: "2024-06-01T12:00:00",
  escalation_time: 0,
  escalation_response_time: 0,
  escalation_update_time: 0,
  escalation_solution_time: 0,
  until_time: 0,
  attachment_count: 0,
  channel: "email",
  has_ai_summary: false,
  ai_escalated: false,
  ai_paused: false,
  archive_flag: 0,
};

async function renderInRouter(ui: React.ReactElement) {
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
    <I18nextProvider i18n={i18n}>
      {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
      <RouterProvider router={router as any} />
    </I18nextProvider>,
  );
}

describe("DashboardTicketRow", () => {
  it("renders number, title, queue, state chip and priority chip", async () => {
    await renderInRouter(<DashboardTicketRow ticket={ticket} />);
    const row = await screen.findByTestId("dashboard-ticket-42");
    expect(row).toHaveTextContent("20240601000042");
    expect(row).toHaveTextContent("Printer on fire");
    // Short queue name (last segment) rather than the full path.
    expect(row).toHaveTextContent("Level 2");
    // Localised state as soft-chip.
    const stateChip = screen.getByTestId("dashboard-ticket-42-state-chip");
    expect(stateChip).toHaveTextContent("Open");
    expect(stateChip).toHaveAttribute("data-kind", "state");
    expect(stateChip).toHaveStyle({ color: "var(--color-state-open)" });
    // Priority soft-chip without the numeric rank.
    const prio = screen.getByTestId("dashboard-ticket-42-priority");
    expect(prio).toHaveTextContent("very high");
    expect(prio).not.toHaveTextContent("5 very");
    expect(prio).toHaveAttribute("data-kind", "priority");
    expect(prio).toHaveStyle({ color: "var(--color-prio-5)" });
    // Old colour-dot / danger-text markup is gone.
    expect(screen.queryByTestId("dashboard-ticket-42-state-dot")).toBeNull();
  });

  it("renders the trailing slot", async () => {
    await renderInRouter(<DashboardTicketRow ticket={ticket} trailing="-2h05m" />);
    expect(await screen.findByText("-2h05m")).toBeInTheDocument();
  });

  it("links to the ticket zoom", async () => {
    await renderInRouter(<DashboardTicketRow ticket={ticket} />);
    const link = await screen.findByTestId("dashboard-ticket-42");
    expect(link.getAttribute("href")).toContain("/agent/tickets/42");
  });

  it("keeps today's look by default: comfortable padding and every meta piece", async () => {
    await renderInRouter(<DashboardTicketRow ticket={ticket} />);
    const link = await screen.findByTestId("dashboard-ticket-42");
    expect(link).toHaveClass("py-2.5");
    expect(link).toHaveAttribute("data-density", "comfortable");
  });

  it("compact density tightens the row; columns choose which pieces show", async () => {
    await renderInRouter(
      <DashboardTicketRow ticket={ticket} density="compact" columns={["title", "state"]} />,
    );
    const link = await screen.findByTestId("dashboard-ticket-42");
    expect(link).toHaveClass("py-1.5");
    expect(link).not.toHaveClass("py-2.5");
    expect(link).toHaveTextContent("Printer on fire");
    expect(link).not.toHaveTextContent("20240601000042");
    expect(link).not.toHaveTextContent("Level 2");
    expect(screen.getByTestId("dashboard-ticket-42-state-chip")).toBeInTheDocument();
    expect(screen.queryByTestId("dashboard-ticket-42-priority")).toBeNull();
  });

  it("can be inert (edit mode) or plain text without a link (preview)", async () => {
    await renderInRouter(<DashboardTicketRow ticket={ticket} linkMode="inert" />);
    expect(await screen.findByTestId("dashboard-ticket-42")).toHaveAttribute("tabindex", "-1");
  });

  it("renders no link at all with linkMode none", async () => {
    await renderInRouter(<DashboardTicketRow ticket={ticket} linkMode="none" />);
    const row = await screen.findByTestId("dashboard-ticket-42");
    expect(row.tagName).not.toBe("A");
    expect(row).toHaveTextContent("Printer on fire");
  });
});
