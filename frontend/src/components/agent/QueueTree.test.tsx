import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { QueueTree, flattenQueues } from "./QueueTree";
import type { QueueNode } from "@/lib/api";

const sample: QueueNode[] = [
  {
    id: 1,
    name: "Raw",
    group_id: 1,
    valid: true,
    counts: { open: 3, new: 0, locked: 1, unlocked: 2, total: 5 },
    children: [
      {
        id: 2,
        name: "Raw::Misc",
        group_id: 1,
        parent_name: "Raw",
        valid: true,
        counts: { open: 1, new: 0, locked: 0, unlocked: 1, total: 1 },
        children: [],
      },
    ],
  },
];

function wrap(ui: React.ReactElement) {
  return render(<I18nextProvider i18n={i18n}>{ui}</I18nextProvider>);
}

describe("QueueTree", () => {
  it("renders queue nodes with open counts", () => {
    wrap(
      <QueueTree queues={sample} selectedId={null} onSelect={() => undefined} />,
    );
    expect(screen.getByTestId("queue-tree")).toBeInTheDocument();
    expect(screen.getByTestId("queue-node-1")).toBeInTheDocument();
    expect(screen.getByTestId("queue-node-2")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("calls onSelect when a queue is clicked", () => {
    const onSelect = vi.fn();
    wrap(<QueueTree queues={sample} selectedId={null} onSelect={onSelect} />);
    fireEvent.click(screen.getByTestId("queue-node-2"));
    expect(onSelect).toHaveBeenCalledWith(2);
  });

  it("highlights selected queue and all-queues", () => {
    const onSelect = vi.fn();
    wrap(<QueueTree queues={sample} selectedId={1} onSelect={onSelect} />);
    fireEvent.click(screen.getByTestId("queue-node-all"));
    expect(onSelect).toHaveBeenCalledWith(null);
  });

  it("flattens nested queues", () => {
    expect(flattenQueues(sample).map((q) => q.id)).toEqual([1, 2]);
  });
});
