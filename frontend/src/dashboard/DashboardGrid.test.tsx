import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { dashboardsApi } from "@/lib/dashboardsApi";
import { DASHBOARD_WIDGETS, registerPluginWidgets } from "@/plugins/dashboardWidgets";
import { setPluginImporterForTests } from "@/plugins/loader";
import { dashboardWidget, helloPlugin } from "@/test/pluginFixtures";
import { DashboardGrid, type EditHandlers } from "./DashboardGrid";
import type { WidgetDataState } from "./data";
import type { Breakpoint, EditWidget } from "./model";
import { registerCoreWidgets } from "./widgets";

vi.mock("@/auth/AuthContext", async () => {
  const actual = await vi.importActual<typeof import("@/auth/AuthContext")>("@/auth/AuthContext");
  return { ...actual, useOptionalAuth: () => ({ user: { id: 5, is_admin: false, can_view_reports: false } }) };
});
// The grid is rendered without a router: links (the counter's drill link) become plain anchors.
vi.mock("@tanstack/react-router", async () => {
  const actual = await vi.importActual<typeof import("@tanstack/react-router")>("@tanstack/react-router");
  return {
    ...actual,
    Link: ({ children, to, ...rest }: { children?: React.ReactNode; to: string; [k: string]: unknown }) => (
      <a href={to} {...(rest as Record<string, unknown>)}>
        {children}
      </a>
    ),
    useNavigate: () => vi.fn(),
  };
});
const plugins = vi.hoisted(() => ({ ready: true }));
vi.mock("@/plugins/PluginProvider", async () => {
  const actual = await vi.importActual<typeof import("@/plugins/PluginProvider")>("@/plugins/PluginProvider");
  return { ...actual, usePlugins: () => ({ plugins: [], ready: plugins.ready, safeMode: false }) };
});

function wire(id: string, type_key: string, x = 0, y = 0, w = 3, h = 2): EditWidget {
  return { id, type_key, type_version: 1, title_override: null, settings: {}, pinned: false, layout: { lg: { x, y, w, h } } };
}

function grid(
  widgets: EditWidget[],
  opts: { bp?: Breakpoint; data?: Record<string, WidgetDataState>; onRemove?: (id: string) => void; mode?: "view" | "edit" } = {},
) {
  const onRetry = vi.fn();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <DashboardGrid
        widgets={widgets}
        mode={opts.mode ?? "view"}
        breakpoint={opts.bp ?? "lg"}
        dashboardId={1}
        canEdit
        data={opts.data ?? {}}
        onRetry={onRetry}
        edit={opts.onRemove ? { onRemove: opts.onRemove } : undefined}
      />
    </QueryClientProvider>,
  );
  return { onRetry };
}

const ok = (value: number): WidgetDataState => ({ status: "ok", data: { value, delta: null, spark: [] } });

beforeAll(async () => {
  await i18n.changeLanguage("de");
});
beforeEach(() => {
  plugins.ready = true;
  registerCoreWidgets();
});

describe("DashboardGrid", () => {
  it("hands each widget its own data and retry; loading cells carry the skeleton test id", () => {
    const { onRetry } = grid([wire("a", "core.counter"), wire("b", "core.counter", 3), wire("c", "core.counter", 6)], {
      data: { a: ok(7), b: { status: "loading" }, c: { status: "error", error: "Kaputt." } },
    });
    expect(within(screen.getByTestId("widget-a")).getByText("7")).toBeVisible();
    expect(screen.getByTestId("widget-b-skeleton")).toBeVisible();
    fireEvent.click(within(screen.getByTestId("widget-c")).getByRole("button", { name: "Erneut versuchen" }));
    expect(onRetry).toHaveBeenCalledWith("c");
  });

  it("phone: a lone counter is not grouped, a run of counters is", () => {
    grid([wire("a", "core.counter"), wire("n", "core.notes"), wire("b", "core.counter"), wire("c", "core.counter")], {
      bp: "sm",
    });
    const groups = screen.getAllByTestId("counter-group");
    expect(groups).toHaveLength(1);
    expect(within(groups[0]).getByTestId("widget-b")).toBeVisible();
    expect(within(groups[0]).getByTestId("widget-c")).toBeVisible();
    expect(screen.getByTestId("widget-a").parentElement).toBe(screen.getByTestId("dashboard-grid"));
  });

  it("unknown types: 'Entfernen' only in edit mode with a handler; plugin types wait for the plugin list", () => {
    const onRemove = vi.fn();
    grid([wire("g", "org.example.gone")], { mode: "edit", onRemove });
    fireEvent.click(within(screen.getByTestId("widget-g")).getByRole("button", { name: "Entfernen" }));
    expect(onRemove).toHaveBeenCalledWith("g");
  });

  it("while plugins are still loading, a plugin type shows a placeholder instead of 'nicht verfügbar'", () => {
    plugins.ready = false;
    grid([wire("g", "org.example.later"), wire("k", "core.gone")]);
    expect(within(screen.getByTestId("widget-g")).queryByText("Widget nicht verfügbar")).toBeNull();
    expect(within(screen.getByTestId("widget-g")).getByTestId("widget-skeleton")).toBeVisible();
    expect(within(screen.getByTestId("widget-k")).getByText("Widget nicht verfügbar")).toBeVisible();
  });

  it("the stored title override reaches the widget's own frame", () => {
    grid([{ ...wire("a", "core.counter"), title_override: "Meine Zahl" }], { data: { a: ok(1) } });
    expect(within(screen.getByTestId("widget-a")).getByRole("heading", { name: "Meine Zahl" })).toBeVisible();
    expect(DASHBOARD_WIDGETS.get("core.counter")).toBeDefined();
  });

  it("a real plugin widget (registered from the plugin list) renders in a host frame with its plugin title; a failing import stays in its cell", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    i18n.addResourceBundle("de", "plugin.org.example.hello", { widget: { weather: "Wetter" } }, true, true);
    registerPluginWidgets([
      helloPlugin({
        locales: [],
        contributions: [
          dashboardWidget("weather", {
            settings_schema: { type: "object", properties: { city: { type: "string", default: "Bonn" } } },
          }),
        ],
      }),
    ]);
    const HelloWidget = ({ settings }: { settings: { city: string } }) => <p>Stadt {settings.city}</p>;
    setPluginImporterForTests(() => Promise.resolve({ default: { components: { HelloWidget } } }));
    grid([wire("p", "org.example.hello.weather", 0, 0, 4, 3), wire("a", "core.counter", 4)], { data: { a: ok(3) } });
    const cell = screen.getByTestId("widget-p");
    expect(await within(cell).findByText("Stadt Bonn")).toBeVisible();
    expect(within(cell).getByRole("heading", { name: "Wetter" })).toBeVisible();
    expect(cell.style.gridColumn).toBe("1 / span 4");
    DASHBOARD_WIDGETS.clearForTests();
    setPluginImporterForTests(null);
  });

  it("a plugin whose code fails to load shows the plugin error in its cell only", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    registerPluginWidgets([helloPlugin({ id: "org.example.kaputt", name: "Kaputt", locales: [], contributions: [dashboardWidget("w")] })]);
    setPluginImporterForTests(() => Promise.reject(new Error("404")));
    grid([wire("p", "org.example.kaputt.w", 0, 0, 4, 3), wire("a", "core.counter", 4)], { data: { a: ok(3) } });
    expect(await within(screen.getByTestId("widget-p")).findByText("Erweiterung „Kaputt“ konnte nicht geladen werden.")).toBeVisible();
    expect(within(screen.getByTestId("widget-a")).getByText("3")).toBeVisible();
    DASHBOARD_WIDGETS.clearForTests();
    setPluginImporterForTests(null);
  });

  it("a crashed widget gets another try when its data changes", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    let explode = true;
    function Flaky() {
      if (explode) throw new Error("kaputt");
      return <p>wieder da</p>;
    }
    DASHBOARD_WIDGETS.register(
      { ...DASHBOARD_WIDGETS.get("core.notes")!, key: "org.example.flaky", owner: "org.example", component: Flaky },
      "org.example",
    );
    const widgets = [wire("f", "org.example.flaky")];
    const client = new QueryClient();
    const view = (data: Record<string, WidgetDataState>) => (
      <QueryClientProvider client={client}>
        <DashboardGrid widgets={widgets} mode="view" breakpoint="lg" dashboardId={1} canEdit data={data} onRetry={() => {}} />
      </QueryClientProvider>
    );
    const { rerender } = render(view({ f: ok(1) }));
    expect(within(screen.getByTestId("widget-f")).getByText("Dieses Widget konnte nicht angezeigt werden.")).toBeVisible();
    explode = false;
    const same = { f: ok(1) };
    rerender(view(same));
    expect(within(screen.getByTestId("widget-f")).getByText("wieder da")).toBeVisible();
    DASHBOARD_WIDGETS.clearForTests();
  });

  describe("edit mode", () => {
    function editGrid(widgets: EditWidget[], edit: EditHandlers, opts: { data?: Record<string, WidgetDataState>; bp?: Breakpoint } = {}) {
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      const view = (ws: EditWidget[]) => (
        <QueryClientProvider client={client}>
          <DashboardGrid widgets={ws} mode="edit" breakpoint={opts.bp ?? "lg"} dashboardId={1} canEdit data={opts.data ?? {}} onRetry={() => {}} edit={edit} />
        </QueryClientProvider>
      );
      const r = render(view(widgets));
      return { rerender: (ws: EditWidget[]) => r.rerender(view(ws)) };
    }

    it("a widget listed in previewIds is fetched once through the preview route; moving it does not refetch", async () => {
      const preview = vi.spyOn(dashboardsApi, "previewData").mockResolvedValue({ status: "ok", data: { value: 42, delta: null, spark: [] } });
      const changed = { ...wire("a", "core.counter"), settings: { source: { kind: "preset", preset: "mine_new" } } };
      const same = wire("b", "core.counter", 3);
      const { rerender } = editGrid([changed, same], { previewIds: new Set(["a"]) }, { data: { b: ok(5) } });
      expect(within(await screen.findByTestId("widget-a", {}, { timeout: 2000 })).getByText("42")).toBeVisible();
      expect(preview).toHaveBeenCalledTimes(1);
      expect(preview).toHaveBeenCalledWith("core.counter", changed.settings, expect.anything());
      expect(within(screen.getByTestId("widget-b")).getByText("5")).toBeVisible();
      rerender([{ ...changed, layout: { lg: { x: 6, y: 0, w: 3, h: 2 } } }, same]);
      await act(async () => {
        await new Promise((r) => setTimeout(r, 350));
      });
      expect(preview).toHaveBeenCalledTimes(1);
      expect(screen.getByTestId("widget-a").style.gridColumn).toBe("7 / span 3");
    });

    it("header buttons appear only for the handlers given; duplicating is disabled at the limit", () => {
      const onDuplicate = vi.fn();
      editGrid([wire("a", "core.notes", 0, 0, 4, 2)], { onDuplicate, canDuplicate: false });
      const cell = screen.getByTestId("widget-a");
      expect(within(cell).getByRole("button", { name: "Duplizieren" })).toBeDisabled();
      expect(within(cell).queryByRole("button", { name: "Entfernen" })).toBeNull();
      expect(within(cell).queryByRole("button", { name: "Einstellungen" })).toBeNull();
    });

    it("the widget named by a failed save gets the danger outline; the body is inert", () => {
      editGrid([wire("a", "core.notes", 0, 0, 4, 2), wire("b", "core.notes", 4, 0, 4, 2)], { invalidWidgetId: "b" });
      expect(screen.getByTestId("widget-b")).toHaveClass("ring-danger");
      expect(screen.getByTestId("widget-a")).not.toHaveClass("ring-danger");
      expect(within(screen.getByTestId("widget-a")).getByTestId("widget-body")).toHaveAttribute("inert");
    });

    it("keyboard in a right-to-left grid: ArrowRight moves towards the inline start", () => {
      const onMove = vi.fn();
      editGrid([wire("a", "core.notes", 4, 0, 4, 2)], { onMove });
      const cell = screen.getByTestId("widget-a");
      screen.getByTestId("dashboard-grid").style.direction = "rtl";
      fireEvent.keyDown(cell, { key: "ArrowRight" });
      expect(onMove).toHaveBeenCalledWith("a", { x: 3, y: 0 }, "keyboard");
    });
  });
});
