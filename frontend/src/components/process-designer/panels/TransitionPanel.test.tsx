import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import type { DesignerCatalog, DesignerEntity } from "@/lib/processDesignerApi";
import { TransitionPanel } from "./TransitionPanel";

const updateEntity = vi.fn();
vi.mock("@/lib/processDesignerApi", () => ({
  processDesignerApi: { updateEntity: (...a: unknown[]) => updateEntity(...a) },
}));

const base = (config: Record<string, unknown>): DesignerEntity => ({
  entity_type: "Transition",
  entity_id: "Transition-1",
  id: 1,
  name: "Vollständig",
  version: "v1",
  sync_state: "synced",
  changed_at: "2026-10-01T10:00:00Z",
  changed_by: 1,
  config,
});

const catalog = {
  actions: [],
  condition_types: [],
  condition_modules: ["Kernel::System::ProcessManagement::TransitionValidation::ValidateDemo"],
  interfaces: [],
  permissions: [],
  account_time: false,
  fields: [
    { name: "Queue", label: "Queue", kind: "select", dialog: true, condition: true, options: [{ value: "Raw", label: "Raw" }, { value: "Junk", label: "Junk" }] },
    { name: "DynamicField_Datum", label: "Datum", kind: "Date", dialog: true, condition: true, options: [] },
    { name: "Title", label: "Title", kind: "text", dialog: true, condition: true, options: [] },
  ],
} as unknown as DesignerCatalog;

function renderPanel(config: Record<string, unknown>) {
  const onSaved = vi.fn();
  render(
    <I18nextProvider i18n={i18n}>
      <TransitionPanel transition={base(config)} catalog={catalog} onSaved={onSaved} onReload={vi.fn()} />
    </I18nextProvider>,
  );
  return { onSaved };
}

beforeEach(async () => {
  await i18n.changeLanguage("de");
  updateEntity.mockReset();
});

describe("TransitionPanel", () => {
  it("shows the always-applies note and an empty builder", () => {
    renderPanel({});
    expect(screen.getByTestId("transition-panel-note")).toHaveTextContent("Keine Bedingung bedeutet");
    expect(screen.getByTestId("condition-empty")).toBeInTheDocument();
    expect(screen.getByTestId("transition-panel-save")).toBeDisabled();
  });

  it("adds a group, picks a field with a select value and saves Znuny config", async () => {
    updateEntity.mockResolvedValue(base({}));
    renderPanel({ Foreign: "keep" });
    fireEvent.click(screen.getByTestId("condition-add-group"));
    fireEvent.click(screen.getByTestId("condition-row-0-0-field"));
    fireEvent.click(screen.getByTestId("condition-row-0-0-field-menu-option-Queue"));
    fireEvent.click(screen.getByTestId("condition-row-0-0-value"));
    fireEvent.click(screen.getByTestId("condition-row-0-0-value-menu-option-Junk"));
    fireEvent.click(screen.getByTestId("transition-panel-save"));
    await waitFor(() => expect(updateEntity).toHaveBeenCalledTimes(1));
    const [type, eid, body] = updateEntity.mock.calls[0];
    expect(type).toBe("Transition");
    expect(eid).toBe("Transition-1");
    expect(body.expected_version).toBe("v1");
    expect(body.config).toEqual({
      Foreign: "keep",
      ConditionLinking: "and",
      Condition: { "1": { Type: "and", Fields: { Queue: { Type: "String", Match: "Junk" } } } },
    });
  });

  it("uses a date input for a date field", () => {
    renderPanel({ Condition: { "1": { Type: "and", Fields: { DynamicField_Datum: { Type: "Equal", Match: "2026-10-01" } } } } });
    const input = screen.getByTestId("condition-row-0-0-value") as HTMLInputElement;
    expect(input.type).toBe("date");
    expect(input.value).toBe("2026-10-01");
  });

  it("shows a German error for an invalid Regexp and blocks saving", () => {
    renderPanel({ Condition: { "1": { Type: "and", Fields: { Title: { Type: "Regexp", Match: "^ok$" } } } } });
    fireEvent.change(screen.getByTestId("condition-row-0-0-value"), { target: { value: "(" } });
    expect(screen.getByTestId("condition-row-0-0-regexp-error")).toHaveTextContent("kein gültiger regulärer Ausdruck");
    expect(screen.getByTestId("transition-panel-save")).toBeDisabled();
  });

  it("keeps an unknown Module path and warns", async () => {
    updateEntity.mockResolvedValue(base({}));
    renderPanel({ Condition: { "1": { Type: "and", Fields: { Title: { Type: "Module", Match: "Kernel::Foo" } } } } });
    expect(screen.getByTestId("condition-row-0-0-module-warning")).toBeInTheDocument();
    expect((screen.getByTestId("condition-row-0-0-value") as HTMLInputElement).value).toBe("Kernel::Foo");
    fireEvent.change(screen.getByTestId("transition-panel-name"), { target: { value: "Neu" } });
    fireEvent.click(screen.getByTestId("transition-panel-save"));
    await waitFor(() => expect(updateEntity).toHaveBeenCalled());
    expect(updateEntity.mock.calls[0][2].config.Condition["1"].Fields.Title).toEqual({ Type: "Module", Match: "Kernel::Foo" });
  });

  it("removes the first of two groups and renumbers on save", async () => {
    updateEntity.mockResolvedValue(base({}));
    renderPanel({
      Condition: {
        "1": { Type: "and", Fields: { Title: { Type: "String", Match: "a" } } },
        "2": { Type: "or", Fields: { Title: { Type: "String", Match: "b" } } },
      },
    });
    fireEvent.click(screen.getByTestId("condition-group-0-remove"));
    fireEvent.click(screen.getByTestId("transition-panel-save"));
    await waitFor(() => expect(updateEntity).toHaveBeenCalled());
    expect(updateEntity.mock.calls[0][2].config.Condition).toEqual({
      "1": { Type: "or", Fields: { Title: { Type: "String", Match: "b" } } },
    });
  });
});
