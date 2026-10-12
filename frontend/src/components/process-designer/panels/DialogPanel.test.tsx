import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import type { DesignerCatalog, DesignerEntity } from "@/lib/processDesignerApi";
import { DialogPanel } from "./DialogPanel";

const updateEntity = vi.fn();
vi.mock("@/lib/processDesignerApi", () => ({
  processDesignerApi: { updateEntity: (...a: unknown[]) => updateEntity(...a) },
}));

const dialog: DesignerEntity = {
  entity_type: "ActivityDialog",
  entity_id: "AD-1",
  id: 1,
  name: "Erfassen",
  version: "v1",
  sync_state: "synced",
  changed_at: "2026-10-01T10:00:00Z",
  changed_by: 1,
  config: {
    DescriptionShort: "Erfassen",
    Fields: {
      Title: { Display: "2", DefaultValue: "" },
      Queue: { Display: "1", DefaultValue: "" },
    },
    FieldOrder: ["Title", "Queue"],
    Interface: ["AgentInterface"],
    Permission: "",
    RequiredLock: "0",
  },
};

const field = (name: string, kind: string, options: { value: string; label: string }[] = []) => ({
  name,
  label: name,
  kind,
  dialog: true,
  condition: false,
  options,
});
const catalog = {
  actions: [],
  condition_types: [],
  condition_modules: [],
  interfaces: ["AgentInterface", "CustomerInterface"],
  permissions: ["", "ro", "rw"],
  account_time: true,
  fields: [
    field("Title", "text"),
    field("Queue", "select", [{ value: "Raw", label: "Raw" }]),
    field("Article", "article"),
    field("Owner", "select"),
  ],
} as unknown as DesignerCatalog;

function renderPanel() {
  const onSaved = vi.fn();
  render(
    <I18nextProvider i18n={i18n}>
      <DialogPanel dialog={dialog} catalog={catalog} onSaved={onSaved} onReload={vi.fn()} />
    </I18nextProvider>,
  );
  return { onSaved };
}

beforeEach(async () => {
  await i18n.changeLanguage("de");
  updateEntity.mockReset();
});

describe("DialogPanel", () => {
  it("shows the Queue warning for Display Sichtbar", () => {
    renderPanel();
    expect(screen.getByTestId("dialog-panel-hint-Queue-queueRequired")).toBeInTheDocument();
    expect(screen.queryByTestId("dialog-panel-hint-Title-queueRequired")).toBeNull();
  });

  it("reorders with buttons and saves Znuny config with string Display", async () => {
    updateEntity.mockResolvedValue(dialog);
    renderPanel();
    fireEvent.click(screen.getByTestId("dialog-panel-down-Title"));
    fireEvent.click(screen.getByTestId("dialog-panel-save"));
    await waitFor(() => expect(updateEntity).toHaveBeenCalledTimes(1));
    const [type, eid, body] = updateEntity.mock.calls[0];
    expect(type).toBe("ActivityDialog");
    expect(eid).toBe("AD-1");
    expect(body.expected_version).toBe("v1");
    expect(body.config.FieldOrder).toEqual(["Queue", "Title"]);
    expect(body.config.Fields.Title.Display).toBe("2");
    expect(body.config.Interface).toEqual(["AgentInterface"]);
  });

  it("requires at least one interface and blocks saving", () => {
    renderPanel();
    fireEvent.click(screen.getByTestId("dialog-panel-iface-AgentInterface"));
    expect(screen.getByTestId("dialog-panel-iface-error")).toBeInTheDocument();
    expect(screen.getByTestId("dialog-panel-save")).toBeDisabled();
  });

  it("customer interface plus Owner shows the customer hint; hidden field asks for a default", () => {
    renderPanel();
    fireEvent.click(screen.getByTestId("dialog-panel-add"));
    fireEvent.click(screen.getByTestId("dialog-panel-add-menu-option-Owner"));
    expect(screen.queryByTestId("dialog-panel-hint-Owner-customerHidden")).toBeNull();
    fireEvent.click(screen.getByTestId("dialog-panel-iface-CustomerInterface"));
    expect(screen.getByTestId("dialog-panel-hint-Owner-customerHidden")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("dialog-panel-display-Owner"));
    fireEvent.click(screen.getByTestId("dialog-panel-display-Owner-menu-option-0"));
    expect(screen.getByTestId("dialog-panel-hint-Owner-hiddenNeedsDefault")).toBeInTheDocument();
  });

  it("Article gets channel, visibility and time units", async () => {
    updateEntity.mockResolvedValue(dialog);
    renderPanel();
    fireEvent.click(screen.getByTestId("dialog-panel-add"));
    fireEvent.click(screen.getByTestId("dialog-panel-add-menu-option-Article"));
    expect(screen.getByTestId("dialog-panel-article-time")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("dialog-panel-article-visible"));
    fireEvent.click(screen.getByTestId("dialog-panel-save"));
    await waitFor(() => expect(updateEntity).toHaveBeenCalled());
    const cfg = updateEntity.mock.calls[0][2].config.Fields.Article.Config;
    expect(cfg).toEqual({ CommunicationChannel: "Internal", IsVisibleForCustomer: "1", TimeUnits: "0" });
  });
});
