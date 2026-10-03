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
    attachment_count: 0,
    has_ai_summary: false,
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
  const queuesRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/agent/queues",
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([ticketRoute, queuesRoute]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  await router.load();
  const result = render(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    <RouterProvider router={router as any} />,
  );
  return { ...result, router };
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

  it("marks a ticket the AI handed to a human", async () => {
    await renderTable([makeItem(), makeItem({ id: 12, ai_escalated: true })]);
    expect(await screen.findByTestId("ticket-ai-escalated-badge-12")).toHaveTextContent(
      "AI handed over to team",
    );
    expect(screen.queryByTestId("ticket-ai-escalated-badge-11")).toBeNull();
  });

  it("marks a ticket whose AI autopilot an agent stopped", async () => {
    await renderTable([makeItem(), makeItem({ id: 12, ai_paused: true })]);
    expect(await screen.findByTestId("ticket-ai-stopped-badge-12")).toHaveTextContent("AI stopped");
    expect(screen.queryByTestId("ticket-ai-stopped-badge-11")).toBeNull();
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

describe("TicketTable queue name", () => {
  it("shows the queue name as a chip next to the title", async () => {
    await renderTable([makeItem({ queue_name: "Support" })]);
    expect(screen.getByTestId("ticket-queue-chip-11")).toHaveTextContent("Support");
  });

  it("omits the chip when queue_name is absent", async () => {
    await renderTable([makeItem({ queue_name: undefined })]);
    expect(screen.queryByTestId("ticket-queue-chip-11")).toBeNull();
  });

  it("links the queue name to that queue's default view rather than the ticket row", async () => {
    await renderTable([makeItem({ queue_id: 42, queue_name: "Support" })]);
    const chip = screen.getByTestId("ticket-queue-chip-11");
    expect(chip.tagName).toBe("A");
    const href = chip.getAttribute("href") ?? "";
    expect(href).toContain("/agent/queues");
    expect(href).toContain("queue_id=42");
    // No forced status: the queue opens on its default "Zu tun" segment.
    expect(href).not.toContain("state_type");
  });

  it("omits the queue name inside a single-queue view", async () => {
    await renderTable([makeItem({ queue_name: "Support" })], { hideQueue: true });
    expect(screen.queryByTestId("ticket-queue-chip-11")).toBeNull();
  });

  it("clicking the chip does not also navigate to the ticket detail route", async () => {
    const { router } = await renderTable([makeItem({ queue_id: 42, queue_name: "Support" })]);
    fireEvent.click(screen.getByTestId("ticket-queue-chip-11"));
    await router.load();
    expect(router.state.location.pathname).toBe("/agent/queues");
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

  it("shows the customer number with the customer_email underneath", async () => {
    await renderTable([
      makeItem({ customer_id: "10042", customer_user_id: "bob", customer_email: "bob@example.com" }),
    ]);
    expect(screen.getByTestId("ticket-customer-cell-11")).toHaveTextContent("10042");
    const email = screen.getByTestId("ticket-customer-email-11");
    expect(email).toHaveTextContent("bob@example.com");
    expect(email).toHaveAttribute("title", "bob@example.com");
  });

  it("falls back to customer_user_id as the e-mail when it looks like one and customer_email is absent", async () => {
    await renderTable([
      makeItem({ customer_id: "10042", customer_user_id: "bob@example.com", customer_email: undefined }),
    ]);
    expect(screen.getByTestId("ticket-customer-cell-11")).toHaveTextContent("10042");
    expect(screen.getByTestId("ticket-customer-email-11")).toHaveTextContent("bob@example.com");
  });

  it("does not duplicate the customer number as an e-mail line when no e-mail is known", async () => {
    await renderTable([
      makeItem({ customer_id: "10042", customer_user_id: undefined, customer_email: undefined }),
    ]);
    expect(screen.getByTestId("ticket-customer-cell-11")).toHaveTextContent("10042");
    expect(screen.queryByTestId("ticket-customer-email-11")).toBeNull();
  });

  it("calls onCustomerClick with the customer_id when the cell is clicked", async () => {
    const onCustomerClick = vi.fn();
    await renderTable([makeItem({ customer_id: "10042", customer_user_id: "bob" })], {
      onCustomerClick,
    });
    fireEvent.click(screen.getByTestId("ticket-customer-name-11"));
    expect(onCustomerClick).toHaveBeenCalledWith("10042");
  });

  it("does not navigate to the ticket when the customer cell is clicked", async () => {
    const onCustomerClick = vi.fn();
    const { router } = await renderTable(
      [makeItem({ customer_id: "10042", customer_user_id: "bob" })],
      { onCustomerClick },
    );
    fireEvent.click(screen.getByTestId("ticket-customer-name-11"));
    await router.load();
    expect(router.state.location.pathname).toBe("/");
  });

  it("clicking the cell area next to the customer name still navigates to the ticket", async () => {
    // Regression: the filter handler used to sit on the full-width cell and
    // swallowed row clicks landing right of the name (e2e queue.spec row click).
    const onCustomerClick = vi.fn();
    const { router } = await renderTable(
      [makeItem({ customer_id: "10042", customer_user_id: "bob" })],
      { onCustomerClick },
    );
    fireEvent.click(screen.getByTestId("ticket-customer-cell-11"));
    await router.load();
    expect(onCustomerClick).not.toHaveBeenCalled();
    expect(router.state.location.pathname).toBe("/agent/tickets/11");
  });

  it("does not call onCustomerClick when the ticket has no customer_id", async () => {
    const onCustomerClick = vi.fn();
    await renderTable(
      [makeItem({ customer_id: undefined, customer_user_id: "bob" })],
      { onCustomerClick },
    );
    fireEvent.click(screen.getByTestId("ticket-customer-cell-11"));
    expect(onCustomerClick).not.toHaveBeenCalled();
  });

  it("shows attachment and AI-summary indicators only when present", async () => {
    await renderTable([
      makeItem({ id: 11, attachment_count: 3, has_ai_summary: true } as Partial<TicketListItem>),
      makeItem({ id: 12, tn: "20240601000012", attachment_count: 0, has_ai_summary: false } as Partial<TicketListItem>),
    ]);
    expect(screen.getByTestId("ticket-attachment-indicator-11")).toHaveTextContent("3");
    expect(screen.getByTestId("ticket-summary-indicator-11")).toBeInTheDocument();
    expect(screen.queryByTestId("ticket-attachment-indicator-12")).toBeNull();
    expect(screen.queryByTestId("ticket-summary-indicator-12")).toBeNull();
  });
});

describe("TicketTable selection mode", () => {
  it("without a selection prop, row click navigates (no checkboxes rendered)", async () => {
    await renderTable([makeItem()]);
    expect(screen.queryByTestId("queue-row-check-11")).toBeNull();
    expect(screen.queryByTestId("queue-select-all-page")).toBeNull();
  });

  it("with a selection prop, the checkbox toggles and the row itself still opens the ticket", async () => {
    const onToggleRow = vi.fn();
    const { router } = await renderTable([makeItem()], {
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
    fireEvent.click(checkbox);
    expect(onToggleRow).toHaveBeenCalledWith(11, false);
    expect(router.state.location.pathname).toBe("/");

    fireEvent.click(checkbox, { shiftKey: true });
    expect(onToggleRow).toHaveBeenLastCalledWith(11, true);

    fireEvent.click(screen.getByTestId("ticket-row-11"));
    await router.load();
    expect(router.state.location.pathname).toBe("/agent/tickets/11");
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

  it("stays available alongside row selection — the row click no longer toggles", async () => {
    await renderTable([makeItem()], {
      quickEdit: quickEditProps(),
      selection: {
        selected: new Set([11]),
        onToggleRow: vi.fn(),
        onToggleAllPage: vi.fn(),
        allPageSelected: true,
        somePageSelected: false,
      },
    });

    expect(screen.getByTestId("ticket-row-state-11")).toBeInTheDocument();
    expect(screen.getByTestId("ticket-row-owner-11")).toBeInTheDocument();
  });
});

describe("TicketTable inbox layout", () => {
  it("only shows a priority chip when the priority is not normal", async () => {
    await renderTable([
      makeItem({ id: 11, priority_id: 3, priority: "3 normal" }),
      makeItem({ id: 12, tn: "20240601000012", priority_id: 4, priority: "4 high" }),
    ]);
    expect(screen.queryByTestId("ticket-priority-chip-11")).toBeNull();
    expect(screen.getByTestId("ticket-priority-chip-12")).toHaveTextContent("high");
  });

  it("shows who wrote last under the activity time", async () => {
    await renderTable([
      makeItem({ id: 11, last_sender_type: "customer", last_article_time: "2024-06-02T08:00:00Z" }),
      makeItem({ id: 12, tn: "20240601000012", last_sender_type: "agent" }),
      makeItem({ id: 13, tn: "20240601000013", last_sender_type: null }),
    ]);
    expect(screen.getByTestId("ticket-last-sender-11")).toHaveTextContent("Customer");
    expect(screen.getByTestId("ticket-last-sender-11")).toHaveAttribute("data-sender", "customer");
    expect(screen.getByTestId("ticket-last-sender-12")).toHaveTextContent("Agent");
    expect(screen.queryByTestId("ticket-last-sender-13")).toBeNull();
  });

  it("groups rows under day headers when asked to", async () => {
    const now = new Date();
    const hoursAgo = (h: number) => new Date(now.getTime() - h * 3600_000).toISOString();
    await renderTable(
      [
        makeItem({ id: 11, last_article_time: now.toISOString() }),
        makeItem({ id: 12, tn: "20240601000012", last_article_time: hoursAgo(24 * 20) }),
      ],
      { groupByDay: true, sort: "activity" },
    );
    expect(screen.getByTestId("ticket-table-day-today")).toBeInTheDocument();
    expect(screen.getByTestId("ticket-table-day-older")).toBeInTheDocument();
    expect(screen.queryByTestId("ticket-table-day-yesterday")).toBeNull();
  });

  it("renders the pinned block first and never repeats a pinned ticket below", async () => {
    const onShowAll = vi.fn();
    const pinnedItem = makeItem({ id: 12, tn: "20240601000012", title: "Burning" });
    await renderTable([makeItem({ id: 11 }), pinnedItem], {
      pinned: { items: [pinnedItem], total: 7, onShowAll },
    });

    expect(screen.getByTestId("ticket-table-pinned-head")).toHaveTextContent("7");
    const rows = screen.getAllByTestId(/^ticket-row-\d+$/).map((r) => r.dataset.testid);
    expect(rows).toEqual(["ticket-row-12", "ticket-row-11"]);

    fireEvent.click(screen.getByTestId("ticket-table-pinned-show-all"));
    expect(onShowAll).toHaveBeenCalledTimes(1);
  });

  it("marks an overdue deadline as breached and a close one as approaching", async () => {
    const now = Math.floor(Date.now() / 1000);
    await renderTable([
      makeItem({ id: 11, escalation_response_time: now - 60 }),
      makeItem({ id: 12, tn: "20240601000012", escalation_solution_time: now + 3600 }),
      makeItem({ id: 13, tn: "20240601000013", escalation_solution_time: now + 86400 }),
    ]);
    expect(screen.getByTestId("ticket-escalation-badge-11")).toHaveAttribute("data-level", "breached");
    expect(screen.getByTestId("ticket-escalation-badge-12")).toHaveAttribute("data-level", "approaching");
    expect(screen.queryByTestId("ticket-escalation-badge-13")).toBeNull();
  });

  it("shows a lock icon for locked tickets", async () => {
    await renderTable([makeItem({ lock: "lock" })]);
    expect(screen.getByTestId("ticket-lock-indicator-11")).toBeInTheDocument();
  });

  describe("linked tickets", () => {
    const a = () =>
      makeItem({
        id: 21,
        tn: "2026092610000029",
        links: [{ ticket_id: 23, tn: "2026092610000038", link_type: "Normal", role: null }],
      });
    const mid = () => makeItem({ id: 22, tn: "2026092610000030" });
    const b = () =>
      makeItem({
        id: 23,
        tn: "2026092610000038",
        links: [{ ticket_id: 21, tn: "2026092610000029", link_type: "Normal", role: null }],
      });

    it("shows a link chip and highlights the partner row on hover", async () => {
      localStorage.removeItem("tiqora.queue.groupLinked");
      await renderTable([a(), mid(), b()]);
      expect(screen.queryByTestId("ticket-links-chip-22")).toBeNull();
      fireEvent.mouseEnter(screen.getByTestId("ticket-links-chip-21"));
      expect(screen.getByTestId("ticket-row-23").className).toContain("ring-accent");
      expect(screen.getByTestId("ticket-row-22").className).not.toContain("ring-accent ");
      fireEvent.mouseLeave(screen.getByTestId("ticket-links-chip-21"));
      expect(screen.getByTestId("ticket-row-23").className).not.toContain("bg-accent-dim");
    });

    it("groups linked tickets on demand and remembers the choice", async () => {
      localStorage.removeItem("tiqora.queue.groupLinked");
      await renderTable([a(), mid(), b()]);
      const order = () =>
        screen.getAllByTestId(/^ticket-row-\d+$/).map((r) => r.dataset.testid);
      expect(order()).toEqual(["ticket-row-21", "ticket-row-22", "ticket-row-23"]);
      fireEvent.click(screen.getByTestId("ticket-table-group-linked"));
      expect(order()).toEqual(["ticket-row-21", "ticket-row-23", "ticket-row-22"]);
      expect(localStorage.getItem("tiqora.queue.groupLinked")).toBe("1");
      localStorage.removeItem("tiqora.queue.groupLinked");
    });

    it("marks a merged ticket with a chip to its main ticket", async () => {
      await renderTable([
        makeItem({
          id: 31,
          state: "merged",
          state_type: "merged",
          merged_into_id: 32,
          merged_into_tn: "2026092610000038",
        }),
      ]);
      expect(screen.getByTestId("ticket-merged-chip-31")).toHaveTextContent("038");
    });

    it("offers no grouping toggle when nothing is linked", async () => {
      await renderTable([makeItem()]);
      expect(screen.queryByTestId("ticket-table-group-linked")).toBeNull();
    });
  });
});
