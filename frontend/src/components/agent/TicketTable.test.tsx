import type { ComponentProps } from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
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

async function renderTable(
  items: TicketListItem[],
  extraProps: Partial<ComponentProps<typeof TicketTable>> = {},
) {
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
        {...extraProps}
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

describe("TicketTable customer cell", () => {
  it("shows the customer_user_id when a customer is assigned", async () => {
    await renderTable([makeItem({ customer_user_id: "bob", first_from: "alice@example.com" })]);
    expect(screen.getByTestId("ticket-customer-cell-11")).toHaveTextContent("bob");
    expect(screen.queryByTestId("ticket-sender-fallback-11")).toBeNull();
  });

  it("falls back to the first article's sender when no customer is assigned", async () => {
    await renderTable([
      makeItem({
        customer_user_id: undefined,
        customer_id: undefined,
        first_from: '"Alice Example" <alice@example.com>',
      }),
    ]);
    const fallback = screen.getByTestId("ticket-sender-fallback-11");
    expect(fallback).toHaveTextContent("Alice Example");
    expect(fallback).toHaveAttribute("title", "Sender of the first article — no customer assigned");
  });

  it("shows a dash when neither a customer nor a first_from are present", async () => {
    await renderTable([makeItem({ customer_user_id: undefined, customer_id: undefined, first_from: undefined })]);
    expect(screen.getByTestId("ticket-customer-cell-11")).toHaveTextContent("—");
    expect(screen.queryByTestId("ticket-sender-fallback-11")).toBeNull();
  });
});

describe("TicketTable selection mode", () => {
  it("without a selection prop, row click navigates (no checkboxes rendered)", async () => {
    await renderTable([makeItem()]);
    expect(screen.queryByTestId("queue-row-check-11")).toBeNull();
    expect(screen.queryByTestId("queue-select-all-page")).toBeNull();
  });

  it("with a selection prop, row click toggles the row instead of navigating", async () => {
    const onToggleRow = vi.fn();
    await renderTable([makeItem()], {
      selection: {
        selected: new Set(),
        onToggleRow,
        onToggleAllPage: vi.fn(),
        allPageSelected: false,
        somePageSelected: false,
      },
    });

    const checkbox = await screen.findByTestId("queue-row-check-11");
    expect(checkbox).not.toBeChecked();

    fireEvent.click(screen.getByTestId("ticket-row-11"));
    expect(onToggleRow).toHaveBeenCalledWith(11);
  });

  it("header checkbox reflects allPageSelected/somePageSelected and calls onToggleAllPage", async () => {
    const onToggleAllPage = vi.fn();
    await renderTable([makeItem(), makeItem({ id: 12, tn: "20240601000012" })], {
      selection: {
        selected: new Set([11]),
        onToggleRow: vi.fn(),
        onToggleAllPage,
        allPageSelected: false,
        somePageSelected: true,
      },
    });

    const headerCheckbox = await screen.findByTestId("queue-select-all-page");
    expect((headerCheckbox as HTMLInputElement).indeterminate).toBe(true);

    fireEvent.click(headerCheckbox);
    expect(onToggleAllPage).toHaveBeenCalledTimes(1);
  });
});

describe("TicketTable quick edit", () => {
  function quickEditProps(overrides: Partial<ComponentProps<typeof TicketTable>["quickEdit"]> = {}) {
    return {
      stateItems: [
        { value: 1, label: "New" },
        { value: 4, label: "Open" },
      ],
      priorityItems: [
        { value: 3, label: "3 normal" },
        { value: 4, label: "4 high" },
      ],
      agentItems: [
        { value: 1, label: "Ada Agent", hint: "ada" },
        { value: 2, label: "Bob Agent", hint: "bob" },
      ],
      onRequestOptions: vi.fn(),
      onPatch: vi.fn(),
      ...overrides,
    };
  }

  it("clicking the state cell opens the listbox and patches state_id without navigating", async () => {
    const onPatch = vi.fn();
    const onRequestOptions = vi.fn();
    await renderTable([makeItem()], {
      quickEdit: quickEditProps({ onPatch, onRequestOptions }),
    });

    fireEvent.click(screen.getByTestId("ticket-row-state-11"));
    expect(onRequestOptions).toHaveBeenCalledTimes(1);

    const option = await screen.findByTestId("ticket-row-state-menu-11-option-4");
    fireEvent.click(option);

    expect(onPatch).toHaveBeenCalledWith(11, { state_id: 4 });
    // No route change: still on "/".
    expect(window.location.pathname).toBe("/");
  });

  it("clicking the owner cell opens a searchable listbox and patches owner_id", async () => {
    const onPatch = vi.fn();
    await renderTable([makeItem()], {
      quickEdit: quickEditProps({ onPatch }),
    });

    fireEvent.click(screen.getByTestId("ticket-row-owner-11"));
    const option = await screen.findByTestId("ticket-row-owner-menu-11-option-2");
    fireEvent.click(option);

    expect(onPatch).toHaveBeenCalledWith(11, { owner_id: 2 });
  });

  it("is disabled while selection mode is active — cells render read-only", async () => {
    await renderTable([makeItem()], {
      quickEdit: quickEditProps(),
      selection: {
        selected: new Set(),
        onToggleRow: vi.fn(),
        onToggleAllPage: vi.fn(),
        allPageSelected: false,
        somePageSelected: false,
      },
    });

    expect(screen.queryByTestId("ticket-row-state-11")).toBeNull();
    expect(screen.queryByTestId("ticket-row-priority-11")).toBeNull();
    expect(screen.queryByTestId("ticket-row-owner-11")).toBeNull();
    // The chip/testids used elsewhere still render, just non-interactive.
    expect(screen.getByTestId("ticket-state-chip-11")).toBeInTheDocument();
  });
});
