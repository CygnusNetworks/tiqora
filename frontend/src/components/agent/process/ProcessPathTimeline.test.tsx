import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { ProcessPathTimeline } from "./ProcessPathTimeline";
import type { ProcessPath } from "@/lib/processApi";

const path: ProcessPath = {
  process_entity_id: "Process-1",
  steps: [
    { activity_entity_id: "A1", activity_name: "Intake", entered_at: "2026-10-01T08:00:00Z", entered_by: 1, entered_by_name: "Ada Example", current: false },
    { activity_entity_id: "A2", activity_name: "", entered_at: "2026-10-02T09:30:00Z", entered_by: 2, entered_by_name: "", current: false },
    { activity_entity_id: "A3", activity_name: "Review", entered_at: "2026-10-03T10:00:00Z", entered_by: 1, entered_by_name: "Ada Example", current: true },
  ],
};

const wrap = (p: ProcessPath) =>
  render(
    <I18nextProvider i18n={i18n}>
      <ProcessPathTimeline path={p} />
    </I18nextProvider>,
  );

describe("ProcessPathTimeline", () => {
  it("lists steps oldest first, marks current, shows '(gelöscht)' for empty names", async () => {
    await i18n.changeLanguage("de");
    wrap(path);
    const items = screen.getAllByTestId("process-path-step");
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveTextContent("Intake");
    expect(items[0]).toHaveTextContent("von Ada Example");
    expect(items[1]).toHaveTextContent("(gelöscht)");
    expect(items[2]).toHaveTextContent("Review");
    expect(within(items[2]).getByText("Aktuell")).toBeInTheDocument();
    expect(items[2]).toHaveAttribute("aria-current", "step");
    expect(items[0]).not.toHaveAttribute("aria-current");
    expect(within(items[0]).queryByText("Aktuell")).toBeNull();
  });

  it("empty state", async () => {
    await i18n.changeLanguage("de");
    wrap({ process_entity_id: "Process-1", steps: [] });
    expect(screen.getByTestId("process-path-empty")).toHaveTextContent("Noch keine Schritte aufgezeichnet.");
  });
});
