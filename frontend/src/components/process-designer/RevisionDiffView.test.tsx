import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import type { RevisionDiff } from "@/lib/processDesignerApi";
import { RevisionDiffView } from "./RevisionDiffView";

const diff: RevisionDiff = {
  from_revision: 3,
  to_revision: 4,
  process_entity_id: "Process-1",
  changes: [
    { change: "added", entity_type: "Activity", entity_id: "Activity-new", name: "Neuer Schritt", changed_keys: [] },
    { change: "removed", entity_type: "Transition", entity_id: "Transition-old", name: "Alter Übergang", changed_keys: [] },
    { change: "changed", entity_type: "Process", entity_id: "Process-1", name: "Bestellung", changed_keys: ["Description"] },
  ],
  yaml_before: "Name: Bestellung\nDescription: alt\nGone: 1\n",
  yaml_after: "Name: Bestellung\nDescription: neu\nNew: 2\n",
};

function renderDiff(d: RevisionDiff = diff) {
  return render(
    <I18nextProvider i18n={i18n}>
      <RevisionDiffView diff={d} />
    </I18nextProvider>,
  );
}

describe("RevisionDiffView", () => {
  it("lists added, removed and changed entities with the changed keys", () => {
    renderDiff();
    expect(screen.getByTestId("revision-diff-change-added")).toHaveTextContent("Neuer Schritt");
    expect(screen.getByTestId("revision-diff-change-removed")).toHaveTextContent("Alter Übergang");
    const changed = screen.getByTestId("revision-diff-change-changed");
    expect(changed).toHaveTextContent("Bestellung");
    expect(changed).toHaveTextContent("Description");
  });

  it("shows removed lines on the left and added lines on the right, aligned with blanks", () => {
    renderDiff();
    const before = screen.getByTestId("revision-diff-before");
    const after = screen.getByTestId("revision-diff-after");
    const removed = Array.from(before.querySelectorAll('[data-kind="removed"],[data-kind="changed"]')).map((e) => e.textContent);
    const added = Array.from(after.querySelectorAll('[data-kind="added"],[data-kind="changed"]')).map((e) => e.textContent);
    expect(removed).toEqual(["- Description: alt", "- Gone: 1"]);
    expect(added).toEqual(["+ Description: neu", "+ New: 2"]);
    // both sides have the same number of rows
    expect(before.children.length).toBe(after.children.length);
    expect(within(before).queryByText(/New: 2/)).not.toBeInTheDocument();
  });

  it("says so when nothing differs", () => {
    renderDiff({ ...diff, changes: [], yaml_after: diff.yaml_before });
    expect(screen.getByTestId("revision-diff-empty")).toBeInTheDocument();
    expect(screen.getByTestId("revision-diff-before").querySelector('[data-kind="removed"]')).toBeNull();
  });
});
