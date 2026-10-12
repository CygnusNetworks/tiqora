import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { dashboardsApi, type DashboardFilters, type WidgetResult } from "@/lib/dashboardsApi";
import { DashboardFiltersProvider } from "../filters/filtersContext";
import { DASHBOARD_WIDGETS, defineWidget, type WidgetDefinition } from "@/plugins/dashboardWidgets";
import { createEditReducer, initialEditState } from "../layout/session";
import type { EditWidget } from "../model";
import { registerCoreWidgets } from "../widgets";
import { SettingsDrawer } from "./SettingsDrawer";
import { makeUser } from "@/test/fireUser";
import { demoSavedSearches, demoTicketFilterFields } from "@/demo/savedMock";
import { savedSearchesApi } from "@/lib/savedSearchesApi";


const listWidget: EditWidget = {
  id: "w-list-0001",
  type_key: "core.ticket-list",
  type_version: 1,
  title_override: "Meine offenen Tickets",
  settings: {},
  pinned: false,
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
const onPinned = vi.fn();
const onCancel = vi.fn();

type Props = Partial<Parameters<typeof SettingsDrawer>[0]>;

function defOf(key: string): WidgetDefinition {
  const def = DASHBOARD_WIDGETS.get(key);
  if (!def) throw new Error(`no ${key}`);
  return def;
}

/** A page with an opener button and a grid behind the drawer. */
function Page({ widget = listWidget, def, errors, focusSource, closeOnApply = true }: Props & { closeOnApply?: boolean }) {
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
          focusSource={focusSource}
          onApply={(s, t, pinned) => {
            onApply(s, t);
            onPinned(pinned);
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

function mount(props: Props = {}, filters?: DashboardFilters) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const user = makeUser();
  render(
    <QueryClientProvider client={client}>
      <DashboardFiltersProvider filters={filters}>
        <Page {...props} />
      </DashboardFiltersProvider>
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
  onPinned.mockClear();
  onCancel.mockClear();
  preview = vi.spyOn(dashboardsApi, "previewData").mockResolvedValue(okList());
  vi.spyOn(savedSearchesApi, "list").mockResolvedValue(demoSavedSearches());
  vi.spyOn(savedSearchesApi, "ticketFilterFields").mockResolvedValue(demoTicketFilterFields());
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

  it("ticket list: a saved-search card (standard views, then the user's searches, grouped) and an own-filter card", async () => {
    const user = mount();
    const { dialog } = await open(user);
    expect(within(dialog).getByRole("radio", { name: "Gespeicherte Suche" })).toBeChecked();
    expect(within(dialog).getByRole("radio", { name: "Eigener Filter" })).not.toBeChecked();
    const select = within(dialog).getByRole("combobox", { name: "Suche oder Ansicht" });
    const standard = within(dialog).getByRole("group", { name: "Standard" });
    expect(Array.from(standard.querySelectorAll("option")).map((o) => o.textContent)).toEqual([
      "Meine offenen",
      "Meine neuen",
      "Zum Übernehmen",
      "Eskaliert",
      "Von der KI übergeben",
    ]);
    expect(await within(dialog).findByRole("group", { name: "Meine Suchen" })).toBeVisible();
    expect(within(dialog).getByRole("option", { name: "Meine offenen Tickets" })).toBeVisible();
    expect(select).toHaveValue("preset:mine_open");
    await user.selectOptions(select, "Eskaliert");
    await user.click(applyBtn());
    expect(onApply).toHaveBeenCalledWith(expect.objectContaining({ source: { kind: "preset", preset: "escalated" } }), "Meine offenen Tickets");
  });

  it("picking one of the user's saved searches stores its id and marks it selected", async () => {
    const user = mount();
    const { dialog } = await open(user);
    await within(dialog).findByRole("option", { name: "Meine offenen Tickets" });
    await user.selectOptions(within(dialog).getByRole("combobox", { name: "Suche oder Ansicht" }), "Meine offenen Tickets");
    expect(within(dialog).getByRole("combobox", { name: "Suche oder Ansicht" })).toHaveValue("search:11");
    await user.click(applyBtn());
    expect(onApply).toHaveBeenCalledWith(expect.objectContaining({ source: { kind: "saved_search", id: 11 } }), "Meine offenen Tickets");
  });

  it("'Eigener Filter' shows the FilterBuilder with ticket fields and updates settings and preview", async () => {
    const user = mount();
    const { dialog } = await open(user);
    await user.click(within(dialog).getByRole("radio", { name: "Eigener Filter" }));
    expect(within(dialog).getByTestId("filter-builder")).toBeVisible();
    await waitFor(() => expect(within(dialog).getByTestId("filter-builder-add-condition")).not.toBeDisabled());
    await user.click(within(dialog).getByTestId("filter-builder-add-condition"));
    await user.click(within(dialog).getByTestId("filter-builder-row-0-field"));
    await user.click(await screen.findByRole("option", { name: "Titel" }));
    await user.type(within(dialog).getByTestId("filter-builder-row-0-value"), "VPN");
    await waitFor(() =>
      expect(preview).toHaveBeenLastCalledWith("core.ticket-list", expect.objectContaining({ source: expect.objectContaining({ kind: "filter" }) }), expect.anything()),
      { timeout: 3000 },
    );
    await user.click(applyBtn());
    expect(onApply).toHaveBeenCalledWith(
      expect.objectContaining({
        source: { kind: "filter", filter: { version: 1, where: { all: [{ field: "title", op: "eq", value: "VPN" }] } } },
      }),
      "Meine offenen Tickets",
    );
  });

  it("a filter the builder cannot show is displayed read-only and saved unchanged", async () => {
    const deep = { version: 1, where: { all: [{ any: [{ all: [{ field: "tn", op: "eq", value: "1" }] }] }] } };
    const user = mount({ widget: widgetOf("core.ticket-list", { source: { kind: "filter", filter: deep } }) });
    const { dialog } = await open(user);
    expect(await within(dialog).findByText(/zu komplex/)).toBeVisible();
    await user.click(applyBtn());
    expect(onApply.mock.calls[0][0].source.filter).toEqual(deep);
  });

  it("opened from a tile's source action, the focus lands on the source section, not the title", async () => {
    const user = mount({ focusSource: true });
    const { dialog } = await open(user);
    await waitFor(() => expect(within(dialog).getByRole("radio", { name: "Gespeicherte Suche" })).toHaveFocus());
  });

  it("a server message on the saved search id shows under the search card", async () => {
    const user = mount({ widget: widgetOf("core.ticket-list", { source: { kind: "saved_search", id: 99 } }), errors: { "settings.source.id": "Die gespeicherte Suche gibt es nicht." } });
    const { dialog } = await open(user);
    expect(within(dialog).getByText("Die gespeicherte Suche gibt es nicht.")).toBeVisible();
    expect(await within(dialog).findByRole("option", { name: "Suche nicht mehr verfügbar" })).toBeDisabled();
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

  it("ticket list: more than 8 columns block 'Übernehmen' with the server's cap", async () => {
    // 8 stored (two of them beyond the chips), then one more chip: 9
    const eight = ["tn", "title", "customer", "state", "due", "queue", "changed", "created"];
    const user = mount({ widget: widgetOf("core.ticket-list", { columns: eight }) });
    const { dialog } = await open(user);
    expect(applyBtn()).toBeEnabled();
    const unchecked = within(dialog).getAllByRole("checkbox", { checked: false });
    await user.click(unchecked[0]);
    expect(within(dialog).getByText("Höchstens 8 Spalten sind möglich.")).toBeVisible();
    expect(applyBtn()).toBeDisabled();
    await user.click(within(dialog).getByRole("checkbox", { name: "Queue" }));
    expect(within(dialog).queryByText("Höchstens 8 Spalten sind möglich.")).toBeNull();
    expect(applyBtn()).toBeEnabled();
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

  it("the counter editor offers the same sources and the tone select", async () => {
    const user = mount({ widget: widgetOf("core.counter", { source: { kind: "preset", preset: "mine_new" }, tone: "ok" }) });
    const { dialog } = await open(user);
    expect(within(dialog).getByRole("radio", { name: "Gespeicherte Suche" })).toBeChecked();
    expect(within(dialog).getByRole("radio", { name: "Eigener Filter" })).toBeVisible();
    expect(within(dialog).getByLabelText("Farbe")).toHaveValue("ok");
    await user.selectOptions(within(dialog).getByRole("combobox", { name: "Suche oder Ansicht" }), "Eskaliert");
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

  it("a server message on the preset or the source shows under the active search card", async () => {
    const user = mount({
      widget: widgetOf("core.ticket-list", { source: { kind: "preset", preset: "mine_open" } }),
      errors: { "settings.source.preset": "Diese Ansicht gibt es nicht." },
    });
    const { dialog } = await open(user);
    expect(within(dialog).getByText("Diese Ansicht gibt es nicht.")).toBeVisible();
    expect(within(dialog).getByLabelText("Suche oder Ansicht")).toHaveAttribute("aria-invalid", "true");
  });

  it("a server message on the sort shows under the sort select", async () => {
    const user = mount({ errors: { "settings.sort": "Diese Sortierung gibt es nicht." } });
    const { dialog } = await open(user);
    expect(within(dialog).getByText("Diese Sortierung gibt es nicht.")).toBeVisible();
    expect(within(dialog).getByLabelText("Sortierung")).toHaveAccessibleDescription("Diese Sortierung gibt es nicht.");
  });

  it("a server message for a field the form does not show is a general message, not lost", async () => {
    const user = mount({
      widget: widgetOf("core.links", { items: [] }),
      errors: { "settings.items": "Höchstens 12 Links sind möglich." },
    });
    const { dialog } = await open(user);
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Höchstens 12 Links sind möglich.");
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

describe("SettingsDrawer: dashboard filters", () => {
  const FILTERS: DashboardFilters = { queue_ids: [3], time_range: null };

  it("shows the switch for filter-aware types only; on means not pinned", async () => {
    const user = mount();
    const { dialog } = await open(user);
    const sw = within(dialog).getByRole("switch", { name: "Dashboard-Filter auf dieses Widget anwenden" });
    expect(sw).toBeChecked();
    cleanup();
    const user2 = mount({ widget: widgetOf("core.notes"), def: defOf("core.notes") });
    const { dialog: d2 } = await open(user2);
    expect(within(d2).queryByRole("switch", { name: "Dashboard-Filter auf dieses Widget anwenden" })).toBeNull();
  });

  it("switching it off hands pinned=true to the host on apply", async () => {
    const user = mount();
    const { dialog } = await open(user);
    await user.click(within(dialog).getByRole("switch", { name: "Dashboard-Filter auf dieses Widget anwenden" }));
    await user.click(applyBtn());
    expect(onPinned).toHaveBeenCalledWith(true);
  });

  it("an already pinned widget opens with the switch off", async () => {
    const user = mount({ widget: { ...listWidget, pinned: true } });
    const { dialog } = await open(user);
    expect(within(dialog).getByRole("switch", { name: "Dashboard-Filter auf dieses Widget anwenden" })).not.toBeChecked();
    await user.click(applyBtn());
    expect(onPinned).toHaveBeenCalledWith(true);
  });

  it("settings preview sends the dashboard filters", async () => {
    const user = mount({}, FILTERS);
    await open(user);
    await waitFor(() => expect(preview).toHaveBeenCalled());
    expect(preview.mock.calls[0][3]).toEqual(FILTERS);
  });

  it("the preview sends no filters when none are active or the widget is switched off", async () => {
    const user = mount();
    await open(user);
    await waitFor(() => expect(preview).toHaveBeenCalled());
    expect(preview.mock.calls[0][3]).toBeUndefined();
    cleanup();
    preview.mockClear();
    const user2 = mount({}, FILTERS);
    const { dialog } = await open(user2);
    await waitFor(() => expect(preview).toHaveBeenCalled());
    preview.mockClear();
    await user2.click(within(dialog).getByRole("switch", { name: "Dashboard-Filter auf dieses Widget anwenden" }));
    await waitFor(() => expect(preview).toHaveBeenCalled());
    expect(preview.mock.calls.at(-1)![3]).toBeUndefined();
  });
});
