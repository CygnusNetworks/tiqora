import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { TicketPropsBar, type TicketPropsBarProps } from "./TicketPropsBar";

const base: TicketPropsBarProps = {
  queues: [
    { id: 1, name: "Support" },
    { id: 2, name: "Level1::Hotline" },
  ],
  queueId: 2,
  onQueueChange: vi.fn(),
  priorities: [
    { id: 3, name: "3 normal" },
    { id: 4, name: "4 high" },
  ],
  priorityId: 3,
  onPriorityChange: vi.fn(),
  states: [
    { id: 4, name: "open", type_name: "open" },
    { id: 2, name: "closed successful", type_name: "closed" },
  ],
  stateId: 2,
  onStateChange: vi.fn(),
};

function renderBar(props: Partial<TicketPropsBarProps> = {}) {
  return render(
    <I18nextProvider i18n={i18n}>
      <TicketPropsBar {...base} {...props} />
    </I18nextProvider>,
  );
}

const owner = (over: Partial<NonNullable<TicketPropsBarProps["owner"]>> = {}) => ({
  agents: [
    { id: 5, login: "agent1", full_name: "Agent One" },
    { id: 6, login: "jwolff", full_name: "Jana Wolff" },
  ],
  value: 5,
  onChange: vi.fn(),
  ...over,
});

describe("TicketPropsBar", () => {
  it("e-mail variant: three cells with their values", () => {
    renderBar();
    const bar = screen.getByTestId("ticket-props-bar");
    expect(within(bar).getAllByRole("button")).toHaveLength(3);
    expect(screen.queryByTestId("new-ticket-owner")).not.toBeInTheDocument();
    // Parent path muted in front of the leaf.
    expect(screen.getByTestId("new-ticket-queue-value")).toHaveTextContent("Level1 › Hotline");
    // The numeric rank is not part of the label.
    expect(screen.getByTestId("new-ticket-priority-value")).toHaveTextContent(/^normal$/);
    expect(screen.getByTestId("new-ticket-state-value")).toHaveTextContent("Closed successful");
  });

  it("phone variant: four cells, owner second", () => {
    renderBar({ owner: owner() });
    const cells = within(screen.getByTestId("ticket-props-bar")).getAllByRole("button");
    expect(cells.map((c) => c.getAttribute("data-testid"))).toEqual([
      "new-ticket-queue",
      "new-ticket-owner",
      "new-ticket-priority",
      "new-ticket-state",
    ]);
    expect(screen.getByTestId("new-ticket-owner-value")).toHaveTextContent("Agent One");
  });

  it("shows source lines only when given", () => {
    const { rerender } = renderBar({
      queueSource: "like Jane Doe's last ticket",
      owner: owner({ source: "answered the call" }),
    });
    expect(screen.getByTestId("new-ticket-queue-source")).toHaveTextContent(
      "like Jane Doe's last ticket",
    );
    expect(screen.getByTestId("new-ticket-owner-source")).toHaveTextContent("answered the call");
    rerender(
      <I18nextProvider i18n={i18n}>
        <TicketPropsBar {...base} queueSource={null} owner={owner()} />
      </I18nextProvider>,
    );
    expect(screen.queryByTestId("new-ticket-queue-source")).not.toBeInTheDocument();
    expect(screen.queryByTestId("new-ticket-owner-source")).not.toBeInTheDocument();
  });

  it("colours priority and state with the theme tokens", () => {
    renderBar();
    const dot = (id: string) =>
      screen.getByTestId(id).querySelector("span[aria-hidden]") as HTMLElement;
    expect(dot("new-ticket-priority").style.background).toBe("var(--color-prio-3)");
    expect(dot("new-ticket-state").style.background).toBe("var(--color-state-closed)");
  });

  it("the whole cell opens the menu and a pick calls onChange", () => {
    const onQueueChange = vi.fn();
    const onStateChange = vi.fn();
    const ownerChange = vi.fn();
    renderBar({ onQueueChange, onStateChange, owner: owner({ onChange: ownerChange }) });

    fireEvent.click(screen.getByTestId("new-ticket-queue"));
    fireEvent.click(screen.getByTestId("new-ticket-queue-panel-option-1"));
    expect(onQueueChange).toHaveBeenCalledWith(1);

    fireEvent.click(screen.getByTestId("new-ticket-state"));
    fireEvent.click(screen.getByTestId("new-ticket-state-panel-option-4"));
    expect(onStateChange).toHaveBeenCalledWith(4);

    fireEvent.click(screen.getByTestId("new-ticket-owner"));
    fireEvent.click(screen.getByTestId("new-ticket-owner-panel-option-6"));
    expect(ownerChange).toHaveBeenCalledWith(6);
  });
});
