import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import type { DesignerEntity } from "@/lib/processDesignerApi";
import { ActivityPanel } from "./ActivityPanel";
import { ProcessPanel, type ProcessFields } from "./ProcessPanel";
import { moveItem, renumberDialogs, dialogIdsOf } from "./activityModel";
import type { ProcessShape } from "../canvasModel";

const updateEntity = vi.fn();
vi.mock("@/lib/processDesignerApi", () => ({
  processDesignerApi: { updateEntity: (...a: unknown[]) => updateEntity(...a) },
}));

const ent = (entity_id: string, name: string, config: Record<string, unknown> = {}, type = "ActivityDialog"): DesignerEntity => ({
  entity_type: type as DesignerEntity["entity_type"],
  entity_id,
  id: 1,
  name,
  config,
  version: "v1",
  sync_state: "synced",
  changed_at: "2026-10-01T10:00:00Z",
  changed_by: 1,
});

const dialogs = [ent("AD-1", "Erfassen"), ent("AD-2", "Prüfen"), ent("AD-3", "Freigeben")];
const activity = ent("Activity-a", "Antrag", { ActivityDialog: { "1": "AD-1", "2": "AD-2" }, Other: 1 }, "Activity");

function renderActivity(extra: Partial<Parameters<typeof ActivityPanel>[0]> = {}) {
  const onSaved = vi.fn();
  const createDialog = vi.fn().mockResolvedValue("AD-9");
  render(
    <I18nextProvider i18n={i18n}>
      <ActivityPanel
        activity={activity}
        dialogs={[...dialogs, ent("AD-9", "Neues Formular")]}
        createDialog={createDialog}
        onSaved={onSaved}
        onReload={vi.fn()}
        {...extra}
      />
    </I18nextProvider>,
  );
  return { onSaved, createDialog };
}

beforeEach(async () => {
  await i18n.changeLanguage("de");
  updateEntity.mockReset();
});

describe("activityModel", () => {
  it("renumbers and reads dialogs in key order", () => {
    expect(renumberDialogs(["b", "a"])).toEqual({ "1": "b", "2": "a" });
    expect(dialogIdsOf({ ActivityDialog: { "10": "z", "2": "y" } })).toEqual(["y", "z"]);
    expect(moveItem(["a", "b", "c"], 2, -1)).toEqual(["a", "c", "b"]);
    expect(moveItem(["a", "b"], 0, -1)).toEqual(["a", "b"]);
  });
});

describe("ActivityPanel", () => {
  it("reorder sends renumbered ActivityDialog keys and keeps other config", async () => {
    updateEntity.mockResolvedValue({ ...activity, version: "v2" });
    const { onSaved } = renderActivity();
    const save = screen.getByTestId("activity-panel-save");
    expect(save).toBeDisabled();
    fireEvent.click(screen.getByTestId("activity-panel-down-AD-1"));
    expect(save).toBeEnabled();
    const items = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(items[0]).toContain("Prüfen");
    fireEvent.click(save);
    await waitFor(() => expect(updateEntity).toHaveBeenCalledTimes(1));
    expect(updateEntity).toHaveBeenCalledWith("Activity", "Activity-a", {
      expected_version: "v1",
      name: "Antrag",
      config: { ActivityDialog: { "1": "AD-2", "2": "AD-1" }, Other: 1 },
    });
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
  });

  it("up/down buttons are real keyboard-focusable buttons with names; edges are disabled", () => {
    renderActivity();
    expect(screen.getByTestId("activity-panel-up-AD-1")).toBeDisabled();
    expect(screen.getByTestId("activity-panel-down-AD-2")).toBeDisabled();
    expect(screen.getByRole("button", { name: "„Erfassen“ nach unten" })).toBeEnabled();
  });

  it("removes a form and adds a new one", async () => {
    updateEntity.mockResolvedValue(activity);
    const { createDialog } = renderActivity();
    fireEvent.click(screen.getByTestId("activity-panel-remove-AD-1"));
    fireEvent.click(screen.getByTestId("activity-panel-new"));
    await waitFor(() => expect(createDialog).toHaveBeenCalled());
    await screen.findByTestId("activity-panel-form-AD-9");
    fireEvent.click(screen.getByTestId("activity-panel-save"));
    await waitFor(() => expect(updateEntity).toHaveBeenCalled());
    expect((updateEntity.mock.calls[0][2] as { config: { ActivityDialog: unknown } }).config.ActivityDialog).toEqual({
      "1": "AD-2",
      "2": "AD-9",
    });
  });

  it("409 shows the conflict message and keeps the local edit", async () => {
    updateEntity.mockRejectedValue(new ApiError(409, "version", "/x"));
    renderActivity();
    fireEvent.click(screen.getByTestId("activity-panel-down-AD-1"));
    fireEvent.click(screen.getByTestId("activity-panel-save"));
    expect(await screen.findByTestId("activity-panel-conflict")).toHaveTextContent(
      i18n.t("admin.processDesigner.conflict"),
    );
    expect(screen.getByTestId("activity-panel-save")).toBeEnabled();
  });

  it("Escape discards the unsaved edits of the panel", () => {
    renderActivity();
    const input = screen.getByTestId("activity-panel-name");
    fireEvent.change(input, { target: { value: "Anders" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(screen.getByTestId("activity-panel-name")).toHaveValue("Antrag");
    expect(screen.getByTestId("activity-panel-save")).toBeDisabled();
  });

  it("Escape on an open add-menu closes only the menu and keeps the edits", async () => {
    const library = [
      { entity_type: "ActivityDialog" as const, entity_id: "AD-77", name: "Fremdes Formular", used_by: ["Activity-z"], sync_state: "synced" as const },
    ];
    renderActivity({ library, adoptDialog: vi.fn().mockResolvedValue(undefined) });
    fireEvent.change(screen.getByTestId("activity-panel-name"), { target: { value: "Anders" } });
    fireEvent.click(screen.getByTestId("activity-panel-add"));
    await screen.findByTestId("activity-panel-add-menu-option-AD-77");
    fireEvent.keyDown(screen.getByTestId("activity-panel-add"), { key: "Escape" });
    expect(screen.getByTestId("activity-panel-name")).toHaveValue("Anders");
    expect(screen.queryByTestId("activity-panel-add-menu-option-AD-77")).not.toBeInTheDocument();
    // A second Escape (nothing open any more) discards as before.
    fireEvent.keyDown(screen.getByTestId("activity-panel-name"), { key: "Escape" });
    expect(screen.getByTestId("activity-panel-name")).toHaveValue("Antrag");
  });

  it("offers forms of other processes and loads a picked one before adding it", async () => {
    const adoptDialog = vi.fn().mockResolvedValue(undefined);
    const library = [
      { entity_type: "ActivityDialog" as const, entity_id: "AD-1", name: "Erfassen", used_by: ["Activity-a"], sync_state: "synced" as const },
      { entity_type: "ActivityDialog" as const, entity_id: "AD-77", name: "Fremdes Formular", used_by: ["Activity-z"], sync_state: "synced" as const },
    ];
    renderActivity({ library, adoptDialog });
    fireEvent.click(screen.getByTestId("activity-panel-add"));
    expect(screen.queryByTestId("activity-panel-add-menu-option-AD-1")).not.toBeInTheDocument();
    fireEvent.click(await screen.findByTestId("activity-panel-add-menu-option-AD-77"));
    await waitFor(() => expect(adoptDialog).toHaveBeenCalledWith("AD-77"));
    expect(await screen.findByTestId("activity-panel-form-AD-77")).toHaveTextContent("Fremdes Formular");
  });

  it("discard restores stored order and name", () => {
    renderActivity();
    fireEvent.change(screen.getByTestId("activity-panel-name"), { target: { value: "Anders" } });
    fireEvent.click(screen.getByTestId("activity-panel-discard"));
    expect(screen.getByTestId("activity-panel-name")).toHaveValue("Antrag");
  });
});

describe("ProcessPanel", () => {
  const shape: ProcessShape = {
    path: { "Activity-a": {}, "Activity-b": {} },
    layout: {},
    startActivity: "Activity-a",
    startActivityDialog: "AD-1",
  };
  const fields: ProcessFields = { name: "Bestellung", description: "d", stateEntityId: "S1" };
  const acts = [activity, ent("Activity-b", "Genehmigung", { ActivityDialog: { "1": "AD-3" } }, "Activity")];

  function renderProcess() {
    const onFieldsChange = vi.fn();
    const onShapeChange = vi.fn();
    render(
      <I18nextProvider i18n={i18n}>
        <ProcessPanel
          fields={fields}
          shape={shape}
          activities={acts}
          dialogs={dialogs}
          onFieldsChange={onFieldsChange}
          onShapeChange={onShapeChange}
        />
      </I18nextProvider>,
    );
    return { onFieldsChange, onShapeChange };
  }

  it("edits name, description and state through onFieldsChange", () => {
    const { onFieldsChange } = renderProcess();
    fireEvent.change(screen.getByTestId("process-panel-name"), { target: { value: "Neu" } });
    expect(onFieldsChange).toHaveBeenLastCalledWith({ ...fields, name: "Neu" });
    fireEvent.change(screen.getByTestId("process-panel-description"), { target: { value: "x" } });
    expect(onFieldsChange).toHaveBeenLastCalledWith({ ...fields, description: "x" });
    fireEvent.click(screen.getByTestId("process-panel-state"));
    fireEvent.click(screen.getByTestId("process-panel-state-menu-option-S3"));
    expect(onFieldsChange).toHaveBeenLastCalledWith({ ...fields, stateEntityId: "S3" });
  });

  it("changing the start step picks that step's first form", () => {
    const { onShapeChange } = renderProcess();
    expect(screen.getByTestId("process-panel-state")).toHaveTextContent("Aktiv");
    fireEvent.click(screen.getByTestId("process-panel-start-step"));
    fireEvent.click(screen.getByTestId("process-panel-start-step-menu-option-Activity-b"));
    expect(onShapeChange).toHaveBeenCalledWith({ ...shape, startActivity: "Activity-b", startActivityDialog: "AD-3" });
  });

  it("start form only offers the start step's forms", () => {
    const { onShapeChange } = renderProcess();
    fireEvent.click(screen.getByTestId("process-panel-start-form"));
    expect(screen.queryByTestId("process-panel-start-form-menu-option-AD-3")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("process-panel-start-form-menu-option-AD-2"));
    expect(onShapeChange).toHaveBeenCalledWith({ ...shape, startActivityDialog: "AD-2" });
  });
});
