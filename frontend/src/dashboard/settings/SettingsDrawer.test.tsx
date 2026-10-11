import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { dashboardsApi, type WidgetResult } from "@/lib/dashboardsApi";
import { DASHBOARD_WIDGETS, defineWidget, type WidgetDefinition } from "@/plugins/dashboardWidgets";
import { createEditReducer, initialEditState } from "../layout/session";
import type { EditWidget } from "../model";
import { registerCoreWidgets } from "../widgets";
import { SettingsDrawer } from "./SettingsDrawer";


// @testing-library/user-event is not a dependency: a small stand-in over fireEvent.
const FOCUSABLE = 'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';
function makeUser() {
  return {
    async click(el: Element) {
      fireEvent.click(el);
    },
    async clear(el: Element) {
      fireEvent.change(el, { target: { value: "" } });
    },
    async type(el: Element, text: string) {
      (el as HTMLElement).focus();
      for (const ch of text) fireEvent.change(el, { target: { value: (el as HTMLInputElement).value + ch } });
    },
    async selectOptions(el: Element, label: string) {
      const option = Array.from((el as HTMLSelectElement).options).find((o) => o.text === label || o.value === label);
      if (!option) throw new Error(`no option ${label}`);
      fireEvent.change(el, { target: { value: option.value } });
    },
    async keyboard(keys: string) {
      const key = keys.replace(/[{}]/g, "");
      fireEvent.keyDown(document.activeElement ?? document.body, { key });
    },
    async tab(opts: { shift?: boolean } = {}) {
      const from = document.activeElement ?? document.body;
      const notPrevented = fireEvent.keyDown(from, { key: "Tab", shiftKey: opts.shift === true });
      if (!notPrevented) return;
      const all = Array.from(document.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((e) => !e.closest("[inert]"));
      const i = all.indexOf(from as HTMLElement);
      all[(i + (opts.shift ? -1 : 1) + all.length) % all.length]?.focus();
    },
  };
}

const listWidget: EditWidget = {
  id: "w-list-0001",
  type_key: "core.ticket-list",
  type_version: 1,
  title_override: "Meine offenen Tickets",
  settings: {},
  layout: { lg: { x: 0, y: 0, w: 8, h: 5 } },
};
const widgetOf = (type_key: string, settings: Record<string, unknown> = {}, over: Partial<EditWidget> = {}): EditWidget => ({
  ...listWidget,
  id: `w-${type_key}`,
  type_key,
  title_override: null,
  settings,
  ...over,
});

const okList = (): WidgetResult => ({ status: "ok", data: { items: [], total: 0 } });
let preview: ReturnType<typeof vi.spyOn>;
const onApply = vi.fn();
const onCancel = vi.fn();

type Props = Partial<Parameters<typeof SettingsDrawer>[0]>;

function defOf(key: string): WidgetDefinition {
  const def = DASHBOARD_WIDGETS.get(key);
  if (!def) throw new Error(`no ${key}`);
  return def;
}

/** A page with an opener button and a grid behind the drawer. */
function Page({ widget = listWidget, def, errors, closeOnApply = true }: Props & { closeOnApply?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <div data-testid="dashboard-grid">
        <button type="button" onClick={() => setOpen(true)}>
          Öffnen
        </button>
      </div>
      {open && (
        <SettingsDrawer
          widget={widget}
          def={def ?? defOf(widget.type_key)}
          errors={errors}
          onApply={(s, t) => {
            onApply(s, t);
            if (closeOnApply) setOpen(false);
          }}
          onCancel={() => {
            onCancel();
            setOpen(false);
          }}
        />
      )}
    </>
  );
}

function mount(props: Props = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const user = makeUser();
  render(
    <QueryClientProvider client={client}>
      <Page {...props} />
    </QueryClientProvider>,
  );
  return user;
}

async function open(user: ReturnType<typeof makeUser>) {
  const opener = screen.getByRole("button", { name: "Öffnen" });
  opener.focus();
  await user.click(opener);
  return { opener, dialog: await screen.findByRole("dialog", { name: "Einstellungen" }) };
}

const applyBtn = () => screen.getByRole("button", { name: "Übernehmen" });

beforeAll(async () => {
  await i18n.changeLanguage("de");
});
beforeEach(() => {
  registerCoreWidgets();
  onApply.mockClear();
  onCancel.mockClear();
  preview = vi.spyOn(dashboardsApi, "previewData").mockResolvedValue(okList());
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("SettingsDrawer", () => {
  it("is a labelled dialog with the type name, title field, form, preview and the two buttons", async () => {
    const user = mount();
    const { dialog } = await open(user);
    expect(within(dialog).getByText("Ticketliste")).toBeVisible();
    expect(within(dialog).getByLabelText("Titel")).toHaveValue("Meine offenen Tickets");
    expect(within(dialog).getByRole("region", { name: "Vorschau" })).toBeVisible();
    expect(within(dialog).getByRole("button", { name: "Abbrechen" })).toBeVisible();
    expect(within(dialog).getByRole("button", { name: "Übernehmen" })).toBeVisible();
    expect(within(dialog).getByRole("button", { name: "Schließen" })).toBeVisible();
  });

  it("ticket list: one saved-search card with the five standard views, no own-filter card yet", async () => {
    const user = mount();
    const { dialog } = await open(user);
    expect(within(dialog).getByRole("radio", { name: /Gespeicherte Suche/ })).toBeChecked();
    expect(within(dialog).queryByText("Eigener Filter")).toBeNull();
    const select = within(dialog).getByRole("combobox", { name: "Gespeicherte Suche" });
    const group = select.querySelector("optgroup");
    expect(group).toHaveAttribute("label", "Standard");
    expect(Array.from(group!.querySelectorAll("option")).map((o) => o.textContent)).toEqual([
      "Meine offenen",
      "Meine neuen",
      "Zum Übernehmen",
      "Eskaliert",
      "Von der KI übergeben",
    ]);
    await user.selectOptions(select, "Eskaliert");
    await user.click(applyBtn());
    expect(onApply).toHaveBeenCalledWith(expect.objectContaining({ source: { kind: "preset", preset: "escalated" } }), "Meine offenen Tickets");
  });

  it("ticket list: columns are chips (one stays), sort combos map to sort+order, rows clamp to 1..50, density radio", async () => {
    const user = mount();
    const { dialog } = await open(user);
    await user.click(within(dialog).getByRole("checkbox", { name: "Queue" }));
    await user.selectOptions(within(dialog).getByLabelText("Sortierung"), "Zuletzt geändert");
    const rows = within(dialog).getByLabelText("Anzahl Zeilen");
    await user.clear(rows);
    await user.type(rows, "99");
    await user.click(within(dialog).getByRole("radio", { name: "Kompakt" }));
    await user.click(applyBtn());
    expect(onApply).toHaveBeenCalledWith(
      expect.objectContaining({
        columns: ["tn", "title", "customer", "state", "due", "queue"],
        sort: "changed",
        order: "desc",
        limit: 50,
        density: "compact",
      }),
      "Meine offenen Tickets",
    );
  });

  it("ticket list: the last remaining column cannot be unchecked", async () => {
    const user = mount({ widget: widgetOf("core.ticket-list", { columns: ["tn", "title"] }) });
    const { dialog } = await open(user);
    await user.click(within(dialog).getByRole("checkbox", { name: "Ticket-Titel" }));
    expect(within(dialog).getByRole("checkbox", { name: "Ticketnummer" })).toBeDisabled();
    expect(within(dialog).getByRole("checkbox", { name: "Ticketnummer" })).toBeChecked();
  });

  it("an empty rows field blocks 'Übernehmen' with the range message", async () => {
    const user = mount();
    const { dialog } = await open(user);
    await user.clear(within(dialog).getByLabelText("Anzahl Zeilen"));
    expect(within(dialog).getByLabelText("Anzahl Zeilen")).toHaveAttribute("aria-invalid", "true");
    expect(within(dialog).getByText("Der Wert muss zwischen 1 und 50 liegen.")).toBeVisible();
    expect(applyBtn()).toBeDisabled();
  });

  it("the live preview re-renders the real widget with the draft, debounced 400 ms, through the preview endpoint", async () => {
    const user = mount();
    const { dialog } = await open(user);
    await waitFor(() => expect(preview).toHaveBeenCalledTimes(1));
    vi.useFakeTimers();
    fireEvent.change(within(dialog).getByLabelText("Anzahl Zeilen"), { target: { value: "3" } });
    await act(() => vi.advanceTimersByTimeAsync(399));
    expect(preview).toHaveBeenCalledTimes(1);
    await act(() => vi.advanceTimersByTimeAsync(2));
    expect(preview).toHaveBeenLastCalledWith("core.ticket-list", expect.objectContaining({ limit: 3 }), expect.anything());
    expect(within(screen.getByTestId("settings-preview")).getByTestId("widget-component")).toHaveAttribute("data-mode", "preview");
  });

  it("typing quickly sends one preview request, not one per keystroke", async () => {
    mount();
    const user = makeUser();
    await open(user);
    await waitFor(() => expect(preview).toHaveBeenCalledTimes(1));
    const rows = screen.getByLabelText("Anzahl Zeilen");
    await user.clear(rows);
    await user.type(rows, "12");
    await waitFor(() => expect(preview).toHaveBeenLastCalledWith("core.ticket-list", expect.objectContaining({ limit: 12 }), expect.anything()));
    // "" is invalid and never sent; 1 and 12 may not both have been settled
    expect(preview.mock.calls.every(([, s]: [string, Record<string, unknown>]) => s.limit !== null)).toBe(true);
    expect(preview.mock.calls.length).toBeLessThanOrEqual(2);
  });

  it("the preview is inert: no links, no focusable controls, fixed maximum height", async () => {
    const user = mount();
    await open(user);
    const box = screen.getByTestId("settings-preview");
    expect(within(box).queryAllByRole("link")).toHaveLength(0);
    expect(box.querySelectorAll("a, button, input, [tabindex]:not([tabindex='-1'])")).toHaveLength(0);
    expect(box).toHaveClass("max-h-56", "overflow-hidden");
    expect(box).toHaveAttribute("inert");
  });

  it("a preview error shows the German message in the preview box and does not block 'Übernehmen'", async () => {
    preview.mockResolvedValue({ status: "error", error: "Diese Daten konnten nicht geladen werden." });
    const user = mount();
    await open(user);
    expect(await within(screen.getByTestId("settings-preview")).findByText("Diese Daten konnten nicht geladen werden.")).toBeVisible();
    expect(applyBtn()).toBeEnabled();
  });

  it("Abbrechen, Escape and the close button discard the draft; focus returns to the opener", async () => {
    const user = mount();
    const closers = [
      () => user.click(screen.getByRole("button", { name: "Abbrechen" })),
      () => user.keyboard("{Escape}"),
      () => user.click(screen.getByRole("button", { name: "Schließen" })),
    ];
    for (const close of closers) {
      const { opener } = await open(user);
      await user.clear(screen.getByLabelText("Titel"));
      await user.type(screen.getByLabelText("Titel"), "Anders");
      await close();
      expect(onApply).not.toHaveBeenCalled();
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(opener).toHaveFocus();
    }
    expect(onCancel).toHaveBeenCalledTimes(3);
  });

  it("an empty title means 'use the default title' (titleOverride null)", async () => {
    const user = mount();
    await open(user);
    await user.clear(screen.getByLabelText("Titel"));
    await user.click(applyBtn());
    expect(onApply).toHaveBeenCalledWith(expect.anything(), null);
  });

  it("a generated form for the other core widgets (counter maps the flat preset onto source)", async () => {
    const user = mount({ widget: widgetOf("core.counter", { source: { kind: "preset", preset: "mine_new" }, tone: "ok" }) });
    const { dialog } = await open(user);
    expect(within(dialog).getByTestId("schema-form")).toBeVisible();
    expect(within(dialog).getByText("Welche Tickets?")).toBeVisible();
    await user.click(within(dialog).getByTestId("schema-form-preset"));
    await user.click(await screen.findByTestId("schema-form-preset-menu-option-escalated"));
    await user.click(applyBtn());
    expect(onApply).toHaveBeenCalledWith({ source: { kind: "preset", preset: "escalated" }, tone: "ok" }, null);
  });

  it("a flat core form validates bounds and blocks 'Übernehmen'", async () => {
    const user = mount({ widget: widgetOf("core.escalations", {}) });
    const { dialog } = await open(user);
    fireEvent.change(within(dialog).getByLabelText("Anzahl Einträge"), { target: { value: "99" } });
    expect(within(dialog).getByText("Der Wert muss zwischen 1 und 20 liegen.")).toBeVisible();
    expect(applyBtn()).toBeDisabled();
  });

  describe("plugin widgets", () => {
    const pluginDef = (settingsSchema: Record<string, unknown> | null): WidgetDefinition =>
      defineWidget({
        key: "org.example.weather",
        owner: "org.example",
        version: 1,
        titleKey: "widget.weather.title",
        ns: "plugin.org.example",
        category: "other",
        icon: "cloud",
        size: { default: [3, 2], min: [2, 2], max: [6, 4] },
        settingsSchema,
        dataEndpoint: null,
        refreshOn: [],
        permission: null,
        component: () => <p>Wetter</p>,
      });
    const pw = widgetOf("org.example.weather");

    it("gets a SchemaForm from its settingsSchema (labels from the plugin namespace); without a schema only the title shows", async () => {
      i18n.addResourceBundle("de", "plugin.org.example", { widget: { weather: { fields: { city: { label: "Ort der Messstation" } } } } }, true, true);
      const user = mount({
        widget: pw,
        def: pluginDef({ type: "object", properties: { city: { type: "string", title: "Stadt" } } }),
      });
      await open(user);
      await user.type(screen.getByLabelText("Ort der Messstation"), "Bonn");
      await user.click(applyBtn());
      expect(onApply).toHaveBeenCalledWith({ city: "Bonn" }, null);
      cleanup();
      const user2 = mount({ widget: pw, def: pluginDef(null) });
      await open(user2);
      expect(screen.queryByTestId("schema-form")).toBeNull();
      expect(screen.getByLabelText("Titel")).toBeVisible();
    });
  });

  it("links: rejects unsafe addresses, caps at 12 rows, blocks 'Übernehmen' while invalid, drops unused rows", async () => {
    const user = mount({ widget: widgetOf("core.links", { items: [] }) });
    await open(user);
    await user.click(screen.getByRole("button", { name: "Link hinzufügen" }));
    await user.type(screen.getByLabelText("Bezeichnung 1"), "Böse");
    await user.type(screen.getByLabelText("Adresse 1"), "javascript:alert(1)");
    expect(screen.getByText("Diese Adresse ist nicht erlaubt.")).toBeVisible();
    expect(screen.getByLabelText("Adresse 1")).toHaveAttribute("aria-invalid", "true");
    expect(applyBtn()).toBeDisabled();
    await user.clear(screen.getByLabelText("Adresse 1"));
    await user.type(screen.getByLabelText("Adresse 1"), "https://example.org");
    expect(applyBtn()).toBeEnabled();
    for (let i = 1; i < 12; i++) await user.click(screen.getByRole("button", { name: "Link hinzufügen" }));
    expect(screen.getByRole("button", { name: "Link hinzufügen" })).toBeDisabled();
    await user.click(applyBtn());
    expect(onApply).toHaveBeenCalledWith({ items: [{ label: "Böse", url: "https://example.org" }] }, null);
  });

  it("links: a half-filled row asks for the missing part", async () => {
    const user = mount({ widget: widgetOf("core.links", { items: [{ label: "Wiki", url: "" }] }) });
    await open(user);
    expect(screen.getByText("Bitte tragen Sie eine Adresse ein.")).toBeVisible();
    expect(applyBtn()).toBeDisabled();
  });

  it("notes: counts characters and blocks 'Übernehmen' over 5000", async () => {
    const user = mount({ widget: widgetOf("core.notes", { markdown: "" }) });
    await open(user);
    fireEvent.change(screen.getByLabelText("Notiz"), { target: { value: "a".repeat(5001) } });
    expect(screen.getByText("5001 / 5000")).toHaveClass("text-danger");
    expect(applyBtn()).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Notiz"), { target: { value: "a".repeat(5000) } });
    expect(screen.getByText("5000 / 5000")).not.toHaveClass("text-danger");
    expect(applyBtn()).toBeEnabled();
  });

  it("server-side path errors map onto fields; a general one shows on top; touching the field clears its error", async () => {
    const user = mount({ errors: { "settings.limit": "Der Wert muss zwischen 1 und 50 liegen.", "": "Irgendetwas stimmt nicht." } });
    await open(user);
    expect(screen.getByLabelText("Anzahl Zeilen")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText("Der Wert muss zwischen 1 und 50 liegen.")).toBeVisible();
    expect(screen.getByText("Irgendetwas stimmt nicht.")).toBeVisible();
    await user.clear(screen.getByLabelText("Anzahl Zeilen"));
    await user.type(screen.getByLabelText("Anzahl Zeilen"), "7");
    expect(screen.getByLabelText("Anzahl Zeilen")).not.toHaveAttribute("aria-invalid");
  });

  it("migrates older stored settings on open", async () => {
    const base = defOf("core.ticket-list");
    const def = defineWidget({ ...base, version: 2, migrate: (o: unknown) => ({ ...(o as object), limit: (o as { rows: number }).rows }) });
    const user = mount({ def, widget: { ...listWidget, type_version: 1, settings: { rows: 12 } } });
    await open(user);
    expect(screen.getByLabelText("Anzahl Zeilen")).toHaveValue(12);
  });

  it("traps focus inside the drawer and makes the page behind inert; the page is usable again afterwards", async () => {
    const user = mount();
    await open(user);
    await user.tab({ shift: true });
    expect(screen.getByRole("dialog")).toContainElement(document.activeElement as HTMLElement);
    for (let i = 0; i < 30; i++) await user.tab();
    expect(screen.getByRole("dialog")).toContainElement(document.activeElement as HTMLElement);
    expect(screen.getByTestId("dashboard-grid").closest("[inert]")).not.toBeNull();
    await user.keyboard("{Escape}");
    expect(screen.getByTestId("dashboard-grid").closest("[inert]")).toBeNull();
  });

  it("'Übernehmen' hands the draft over as one 'settings' action (one undo step); nothing is saved", async () => {
    const save = vi.spyOn(dashboardsApi, "save");
    const user = mount();
    await open(user);
    await user.clear(screen.getByLabelText("Titel"));
    await user.type(screen.getByLabelText("Titel"), "Neu");
    await user.click(applyBtn());
    const [settings, title] = onApply.mock.calls[0];
    const reducer = createEditReducer(() => undefined);
    const s0 = initialEditState("Start", [listWidget], 1);
    const s1 = reducer(s0, { type: "settings", id: listWidget.id, settings, titleOverride: title });
    expect(s1.past).toHaveLength(1);
    expect(s1.widgets[0].title_override).toBe("Neu");
    expect(save).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
