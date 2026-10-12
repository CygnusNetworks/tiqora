import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import type { DesignerCatalog, DesignerEntity } from "@/lib/processDesignerApi";
import { ActionPanel } from "./ActionPanel";
import { TransitionActions } from "./TransitionActions";

const updateEntity = vi.fn();
vi.mock("@/lib/processDesignerApi", () => ({
  processDesignerApi: { updateEntity: (...a: unknown[]) => updateEntity(...a) },
}));

const MOD = "Kernel::System::ProcessManagement::TransitionAction::";
const entity = (id: string, name: string, config: Record<string, unknown>): DesignerEntity => ({
  entity_type: "TransitionAction",
  entity_id: id,
  id: 1,
  name,
  version: "v1",
  sync_state: "synced",
  changed_at: "2026-10-01T10:00:00Z",
  changed_by: 1,
  config,
});
const catalog = {
  actions: [
    {
      name: "TicketStateSet",
      module: MOD + "TicketStateSet",
      supported: true,
      tiqora_only: false,
      params: [
        { name: "State", kind: "state", required: true, one_of: ["State", "StateID"], list_value: false },
        { name: "StateID", kind: "state", required: true, one_of: ["State", "StateID"], list_value: false },
      ],
    },
    { name: "ExecuteInvoker", module: MOD + "ExecuteInvoker", supported: false, tiqora_only: false, params: [] },
    { name: "AppointmentCreate", module: MOD + "AppointmentCreate", supported: true, tiqora_only: false, params: [] },
  ],
  fields: [{ name: "State", label: "State", kind: "select", dialog: true, condition: true, options: [{ value: "open", label: "open" }] }],
} as unknown as DesignerCatalog;

function renderPanel(action: DesignerEntity) {
  const onSaved = vi.fn();
  render(
    <I18nextProvider i18n={i18n}>
      <ActionPanel action={action} catalog={catalog} onSaved={onSaved} onReload={vi.fn()} />
    </I18nextProvider>,
  );
  return { onSaved };
}

beforeEach(async () => {
  await i18n.changeLanguage("de");
  updateEntity.mockReset();
});

describe("ActionPanel", () => {
  it("edits State of TicketStateSet and saves Znuny config, keeping raw keys", async () => {
    const action = entity("TA-1", "Schließen", { Module: MOD + "TicketStateSet", Config: { State: "open", DynamicField_X: "1" } });
    updateEntity.mockResolvedValue(action);
    renderPanel(action);
    expect(screen.getByTestId("action-panel-save")).toBeDisabled();
    expect(screen.getByTestId("action-raw-0-key")).toHaveValue("DynamicField_X");
    fireEvent.change(screen.getByTestId("action-field-State|StateID-input"), { target: { value: "<OTRS_TICKET_State>" } });
    fireEvent.change(screen.getByTestId("action-panel-userid"), { target: { value: "5" } });
    fireEvent.click(screen.getByTestId("action-panel-save"));
    await waitFor(() => expect(updateEntity).toHaveBeenCalledTimes(1));
    const [type, eid, body] = updateEntity.mock.calls[0];
    expect(type).toBe("TransitionAction");
    expect(eid).toBe("TA-1");
    expect(body.expected_version).toBe("v1");
    expect(body.config).toEqual({
      Module: MOD + "TicketStateSet",
      Config: { State: "<OTRS_TICKET_State>", DynamicField_X: "1", UserID: 5 },
    });
  });

  it("switches between name and ID alternative", async () => {
    const action = entity("TA-1", "A", { Module: MOD + "TicketStateSet", Config: {} });
    updateEntity.mockResolvedValue(action);
    renderPanel(action);
    fireEvent.change(screen.getByTestId("action-field-State|StateID-mode"), { target: { value: "StateID" } });
    fireEvent.change(screen.getByTestId("action-field-State|StateID-input"), { target: { value: "4" } });
    fireEvent.click(screen.getByTestId("action-panel-save"));
    await waitFor(() => expect(updateEntity).toHaveBeenCalled());
    expect(updateEntity.mock.calls[0][2].config.Config).toEqual({ StateID: "4" });
  });

  it("shows only raw rows for an unknown module", () => {
    renderPanel(entity("TA-2", "Eigene", { Module: MOD + "Custom", Config: { Foo: "bar" } }));
    expect(screen.getByTestId("action-panel-unknown")).toBeInTheDocument();
    expect(screen.queryByTestId("action-field-State|StateID")).toBeNull();
    expect(screen.getByTestId("action-raw-0-value")).toHaveValue("bar");
  });

  it("badges modules Znuny-only", () => {
    renderPanel(entity("TA-3", "Invoker", { Module: MOD + "ExecuteInvoker", Config: {} }));
    expect(screen.getByTestId("action-panel-badge")).toHaveTextContent("nur in Znuny wirksam");
  });
});

describe("TransitionActions", () => {
  it("reorders, removes and adds existing actions via onChange", () => {
    const actions = [entity("A", "Eins", {}), entity("B", "Zwei", {}), entity("C", "Drei", {})];
    const onChange = vi.fn();
    render(
      <I18nextProvider i18n={i18n}>
        <TransitionActions actionIds={["A", "B"]} actions={actions} createAction={vi.fn()} onChange={onChange} onSelect={vi.fn()} />
      </I18nextProvider>,
    );
    fireEvent.click(screen.getByTestId("transition-action-A-down"));
    expect(onChange).toHaveBeenLastCalledWith(["B", "A"]);
    fireEvent.click(screen.getByTestId("transition-action-B-remove"));
    expect(onChange).toHaveBeenLastCalledWith(["A"]);
  });

  it("creates a new action, appends and selects it", async () => {
    const onChange = vi.fn();
    const onSelect = vi.fn();
    render(
      <I18nextProvider i18n={i18n}>
        <TransitionActions actionIds={["A"]} actions={[entity("A", "Eins", {})]} createAction={async () => "N"} onChange={onChange} onSelect={onSelect} />
      </I18nextProvider>,
    );
    fireEvent.click(screen.getByTestId("transition-actions-new"));
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(["A", "N"]));
    expect(onSelect).toHaveBeenCalledWith("N");
  });
});
