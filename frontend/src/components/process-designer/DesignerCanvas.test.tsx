import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { DesignerCanvas, type Selection } from "./DesignerCanvas";
import { EntityPalette } from "./EntityPalette";
import { shapeOf } from "./canvasModel";
import type { ProcessDraft } from "@/lib/processDesignerApi";

beforeAll(() => {
  // React Flow measures nodes with ResizeObserver and reads the viewport matrix; jsdom has neither.
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  vi.stubGlobal("ResizeObserver", RO);
  vi.stubGlobal(
    "DOMMatrixReadOnly",
    class {
      m22 = 1;
      constructor(_t?: string) {}
    },
  );
});

const entity = (type: string, entity_id: string, name: string, config: Record<string, unknown> = {}) => ({
  entity_type: type,
  entity_id,
  id: 1,
  name,
  config,
  version: "v1",
  sync_state: "synced",
  changed_at: "2026-10-01T10:00:00Z",
  changed_by: 1,
});

const draft = {
  process: entity("Process", "Process-1", "Bestellung", {
    StartActivity: "Activity-a",
    StartActivityDialog: "AD-1",
    Path: {
      "Activity-a": { "Transition-1": { ActivityEntityID: "Activity-b", TransitionAction: [] } },
      "Activity-b": {},
    },
  }),
  state_entity_id: "S1",
  state_name: "Active",
  layout: { "Activity-a": { left: "40", top: "40" }, "Activity-b": { left: "300", top: "40" } },
  activities: [
    entity("Activity", "Activity-a", "Antrag erfassen", { ActivityDialog: { "1": "AD-1" } }),
    entity("Activity", "Activity-b", "Genehmigung"),
    entity("Activity", "Activity-c", "Archivieren"),
  ],
  activity_dialogs: [entity("ActivityDialog", "AD-1", "Antrag")],
  transitions: [entity("Transition", "Transition-1", "Vollständig"), entity("Transition", "Transition-2", "Abgelehnt")],
  transition_actions: [],
  missing_references: [],
  pending_changes: 0,
} as unknown as ProcessDraft;

function renderCanvas(selection: Selection) {
  const props = {
    draft,
    shape: shapeOf(draft),
    selection,
    onSelect: vi.fn(),
    onShapeChange: vi.fn(),
    createActivity: vi.fn().mockResolvedValue("Activity-new"),
    createTransition: vi.fn().mockResolvedValue("Transition-new"),
  };
  render(
    <I18nextProvider i18n={i18n}>
      <DesignerCanvas {...props} />
    </I18nextProvider>,
  );
  return props;
}

describe("DesignerCanvas", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("de");
  });

  it("renders one node per activity with names, dialogs and start/end markers", () => {
    renderCanvas(null);
    expect(screen.getAllByTestId("process-designer-node")).toHaveLength(2);
    expect(screen.getByText("Antrag erfassen")).toBeInTheDocument();
    expect(screen.getByText("Antrag")).toBeInTheDocument(); // dialog of the first step
    expect(screen.getByTestId("process-designer-marker-start")).toBeInTheDocument();
    expect(screen.getByTestId("process-designer-marker-end")).toBeInTheDocument();
  });

  it("Delete on a selected step asks first, then removes it from the shape (not the entity)", async () => {
    const props = renderCanvas({ kind: "activity", id: "Activity-b" });
    fireEvent.keyDown(screen.getByTestId("process-designer-canvas"), { key: "Delete" });
    expect(props.onShapeChange).not.toHaveBeenCalled();
    expect(await screen.findByText(/Genehmigung.*aus dem Prozess entfernen/)).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("confirm-dialog-cancel"));
    await waitFor(() => expect(screen.queryByTestId("confirm-dialog")).not.toBeInTheDocument());
    expect(props.onShapeChange).not.toHaveBeenCalled();

    fireEvent.keyDown(screen.getByTestId("process-designer-canvas"), { key: "Delete" });
    await screen.findByTestId("confirm-dialog");
    fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));
    await waitFor(() => expect(props.onShapeChange).toHaveBeenCalledTimes(1));
    const next = props.onShapeChange.mock.calls[0][0];
    expect(Object.keys(next.path)).toEqual(["Activity-a"]);
    expect(next.path["Activity-a"]).toEqual({}); // incoming transition dropped
    expect(props.onSelect).toHaveBeenCalledWith(null);
  });

  it("Delete on a selected transition disconnects it after confirmation", async () => {
    const props = renderCanvas({ kind: "transition", from: "Activity-a", transitionId: "Transition-1" });
    fireEvent.keyDown(screen.getByTestId("process-designer-canvas"), { key: "Delete" });
    expect(await screen.findByText(/Vollständig.*entfernen/)).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));
    await waitFor(() => expect(props.onShapeChange).toHaveBeenCalled());
    expect(props.onShapeChange.mock.calls[0][0].path["Activity-a"]).toEqual({});
  });

  it("Delete without a selection does nothing", () => {
    const props = renderCanvas(null);
    fireEvent.keyDown(screen.getByTestId("process-designer-canvas"), { key: "Delete" });
    expect(screen.queryByTestId("confirm-dialog")).not.toBeInTheDocument();
    expect(props.onShapeChange).not.toHaveBeenCalled();
  });

  it("dropping the palette's new step creates an activity and adds it at the drop position", async () => {
    const props = renderCanvas(null);
    const data: Record<string, string> = { "application/x-tiqora-activity": "" };
    const dataTransfer = {
      types: Object.keys(data),
      getData: (k: string) => data[k],
      dropEffect: "",
    };
    fireEvent.drop(screen.getByTestId("process-designer-canvas"), { dataTransfer, clientX: 100, clientY: 80 });
    await waitFor(() => expect(props.onShapeChange).toHaveBeenCalled());
    expect(props.createActivity).toHaveBeenCalledTimes(1);
    const next = props.onShapeChange.mock.calls[0][0];
    expect("Activity-new" in next.path).toBe(true);
    expect(next.layout["Activity-new"]).toBeDefined();
    expect(props.onSelect).toHaveBeenCalledWith({ kind: "activity", id: "Activity-new" });
  });

  it("dropping an existing activity does not create an entity", async () => {
    const props = renderCanvas(null);
    const data: Record<string, string> = { "application/x-tiqora-activity": "Activity-c" };
    fireEvent.drop(screen.getByTestId("process-designer-canvas"), {
      dataTransfer: { types: Object.keys(data), getData: (k: string) => data[k] },
    });
    await waitFor(() => expect(props.onShapeChange).toHaveBeenCalled());
    expect(props.createActivity).not.toHaveBeenCalled();
    expect("Activity-c" in props.onShapeChange.mock.calls[0][0].path).toBe(true);
  });
});

describe("EntityPalette", () => {
  it("offers a new step and only the activities that are not in the process yet", async () => {
    await i18n.changeLanguage("de");
    render(
      <I18nextProvider i18n={i18n}>
        <EntityPalette activities={draft.activities} usedIds={new Set(["Activity-a", "Activity-b"])} />
      </I18nextProvider>,
    );
    expect(screen.getByTestId("process-designer-palette-new")).toHaveTextContent("Neuer Schritt");
    expect(screen.getByTestId("process-designer-palette-Activity-c")).toHaveTextContent("Archivieren");
    expect(screen.queryByTestId("process-designer-palette-Activity-a")).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "zzz" } });
    expect(screen.queryByTestId("process-designer-palette-Activity-c")).not.toBeInTheDocument();
  });
});
