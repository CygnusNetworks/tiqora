import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { BulkEditPanel } from "./BulkEditPanel";
import type { BulkDraft, BulkRefs } from "@/lib/bulkDraft";

// The panel offers exactly what the caller (QueuesPage, fed by the AgentTicketBulk field
// options) hands it in `refs`: it adds no choices of its own.
const refs: BulkRefs = {
  states: [{ id: 4, name: "open", type_name: "open" }],
  priorities: [{ id: 3, name: "3 normal" }],
  agents: [{ value: 6, label: "Bob Agent" }],
  queues: [{ value: 9, label: "Sales" }],
};

function renderPanel(
  over: Partial<BulkRefs> = {},
  draft: BulkDraft = { owner_id: 6, state_id: 4, queue_id: 9 },
  ownerNotice?: string,
) {
  return render(
    <I18nextProvider i18n={i18n}>
      <BulkEditPanel
        count={2}
        known={[]}
        refs={{ ...refs, ...over }}
        draft={draft}
        onChange={vi.fn()}
        meId={5}
        applying={false}
        onApply={vi.fn()}
        onDiscard={vi.fn()}
        onClose={vi.fn()}
        ownerNotice={ownerNotice}
      />
    </I18nextProvider>,
  );
}

describe("BulkEditPanel pickers", () => {
  it("lists only the given owners, states and queues", () => {
    renderPanel();
    fireEvent.click(screen.getByTestId("queue-bulk-owner-select"));
    expect(screen.getByTestId("queue-bulk-owner-select-menu-option-6")).toBeInTheDocument();
    expect(screen.queryByTestId("queue-bulk-owner-select-menu-option-5")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("queue-bulk-state-select"));
    expect(screen.getByTestId("queue-bulk-state-select-menu-option-4")).toBeInTheDocument();
    expect(screen.queryByTestId("queue-bulk-state-select-menu-option-2")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("queue-bulk-queue-select"));
    expect(screen.getByTestId("queue-bulk-queue-select-menu-option-9")).toBeInTheDocument();
  });

  it("offers no owner at all when the list is empty", () => {
    renderPanel({ agents: [] });
    fireEvent.click(screen.getByTestId("queue-bulk-owner-select"));
    expect(screen.queryByTestId("queue-bulk-owner-select-menu-option-5")).not.toBeInTheDocument();
    expect(screen.queryByTestId("queue-bulk-owner-select-menu-option-6")).not.toBeInTheDocument();
  });

  it("shows the owner notice only when one is given", () => {
    const { unmount } = renderPanel();
    expect(screen.queryByTestId("queue-bulk-owner-notice")).not.toBeInTheDocument();
    unmount();
    renderPanel({}, undefined, "Nur die ersten 500");
    expect(screen.getByTestId("queue-bulk-owner-notice")).toHaveTextContent("Nur die ersten 500");
  });
});
