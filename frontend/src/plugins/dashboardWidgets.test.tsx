import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import { act, render, renderHook, screen } from "@testing-library/react";
import i18n from "@/i18n";
import { dashboardWidget, helloPlugin } from "@/test/pluginFixtures";
import {
  DASHBOARD_WIDGETS,
  defineWidget,
  registerPluginWidgets,
  useDashboardWidgetDefinitions,
  type WidgetProps,
} from "./dashboardWidgets";
import { setPluginImporterForTests } from "./loader";

const withWidget = helloPlugin({
  locales: [],
  contributions: [
    dashboardWidget("weather", {
      title_key: "widget.title",
      size: { default: [4, 2], min: [2, 2], max: [6, 4] },
      settings_schema: { type: "object", properties: { city: { type: "string", default: "Bonn" } } },
    }),
  ],
});

beforeEach(async () => {
  DASHBOARD_WIDGETS.clearForTests();
  await i18n.changeLanguage("de");
});

afterEach(() => {
  setPluginImporterForTests(null);
  vi.restoreAllMocks();
});

describe("dashboard widget point", () => {
  it("registers plugin widgets once with owner and namespace", () => {
    registerPluginWidgets([withWidget]);
    registerPluginWidgets([withWidget]);
    const w = DASHBOARD_WIDGETS.get("org.example.hello.weather")!;
    expect(w.owner).toBe("org.example.hello");
    expect(DASHBOARD_WIDGETS.owner("org.example.hello.weather")).toBe("org.example.hello");
    expect(w.ns).toBe("plugin.org.example.hello");
    expect(w.titleKey).toBe("widget.title");
    expect(w.size).toEqual({ default: [4, 2], min: [2, 2], max: [6, 4] });
    expect(w.settingsSchema).toEqual({ type: "object", properties: { city: { type: "string", default: "Bonn" } } });
    expect(w.dataEndpoint).toBeNull();
    expect(w.permission).toBeNull();
    expect(DASHBOARD_WIDGETS.list().filter((x) => x.owner === "org.example.hello")).toHaveLength(1);
  });

  it("useDashboardWidgetDefinitions re-renders after plugins are registered", () => {
    DASHBOARD_WIDGETS.clearForTests();
    const { result } = renderHook(() => useDashboardWidgetDefinitions().filter((w) => w.owner !== "core"));
    expect(result.current).toHaveLength(0);
    act(() => registerPluginWidgets([withWidget]));
    expect(result.current.map((w) => w.key)).toEqual(["org.example.hello.weather"]);
  });

  it("a key held by another owner is left alone", () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const Core = () => null;
    DASHBOARD_WIDGETS.register(
      defineWidget({
        key: "org.example.hello.weather",
        owner: "core",
        version: 1,
        titleKey: "x",
        category: "other",
        icon: "puzzle",
        size: { default: [4, 3], min: [2, 2], max: [12, 8] },
        settingsSchema: null,
        dataEndpoint: null,
        refreshOn: [],
        permission: null,
        component: Core,
      }),
    );
    expect(() => registerPluginWidgets([withWidget])).not.toThrow();
    expect(DASHBOARD_WIDGETS.get("org.example.hello.weather")?.component).toBe(Core);
  });

  it("typed core widgets register without casts (defineWidget)", () => {
    // tsc fixture (R-WIDGET-GENERIC): a widget with its own settings type.
    const City = ({ settings }: WidgetProps<{ city: string }>) => createElement("p", null, `Stadt ${settings.city}`);
    const def = defineWidget<{ city: string }>({
      key: "core.city",
      owner: "core",
      version: 2,
      titleKey: "dashboard.city",
      category: "tickets",
      icon: "map",
      size: { default: [3, 2], min: [2, 2], max: [6, 4] },
      settingsSchema: null,
      dataEndpoint: "core.city",
      refreshOn: ["ticket.created"],
      permission: null,
      component: City,
      migrate: (old) => ({ city: String((old as { town?: string }).town ?? "") }),
    });
    DASHBOARD_WIDGETS.register(def);
    const got = DASHBOARD_WIDGETS.get("core.city")!;
    expect(got.component).toBe(City);
    expect(got.migrate?.({ town: "Bonn" }, 1)).toEqual({ city: "Bonn" });
  });

  it("the plugin widget component loads the plugin's component inside a boundary", async () => {
    const HelloWidget = ({ settings, mode }: WidgetProps<{ city: string }>) =>
      createElement("p", null, `Stadt ${settings.city} (${mode})`);
    setPluginImporterForTests(() => Promise.resolve({ default: { components: { HelloWidget } } }));
    registerPluginWidgets([withWidget]);
    const Widget = DASHBOARD_WIDGETS.get("org.example.hello.weather")!.component;
    render(
      createElement(Widget, {
        instanceId: "home-org.example.hello.weather",
        settings: { city: "Bonn" },
        size: { w: 4, h: 2 },
        filters: {},
        mode: "view",
      }),
    );
    expect(await screen.findByText("Stadt Bonn (view)")).toBeInTheDocument();
  });

  it("a broken plugin widget shows the boundary message", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    setPluginImporterForTests(() => Promise.reject(new Error("404")));
    registerPluginWidgets([withWidget]);
    const Widget = DASHBOARD_WIDGETS.get("org.example.hello.weather")!.component;
    render(createElement(Widget, { instanceId: "x", settings: {}, size: { w: 4, h: 2 }, filters: {}, mode: "view" }));
    expect(await screen.findByText("Erweiterung „Hallo“ konnte nicht geladen werden.")).toBeInTheDocument();
  });
});
