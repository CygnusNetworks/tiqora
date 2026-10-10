import { describe, expect, it } from "vitest";
import { renderHook } from "@testing-library/react";
import { DASHBOARD_WIDGETS, type WidgetDefinition, type WidgetProps } from "@/plugins/dashboardWidgets";
import {
  COLUMN_KEYS,
  canUseWidget,
  defineCoreWidget,
  galleryGroupOf,
  settingsWithDefaults,
  type CoreWidgetSpec,
} from "./definitions";
import { useWidgetData } from "./data";

/** A complete spec; tests override single fields. */
function spec(over: Partial<CoreWidgetSpec<Record<string, unknown>>> = {}): CoreWidgetSpec<Record<string, unknown>> {
  return {
    key: "core.x",
    version: 2,
    title: "dashboards.widgets.x.title",
    category: "tickets",
    icon: "list",
    size: { default: [3, 2], min: [2, 1], max: [6, 4] },
    settings: { type: "object", properties: {} },
    data: { endpoint: "core.x" },
    refreshOn: ["ticket_changed"],
    component: () => null,
    ...over,
  };
}

/** A complete shared definition for the pure helpers. */
function anyDef(over: Partial<WidgetDefinition> = {}): WidgetDefinition {
  return {
    key: "core.any",
    owner: "core",
    version: 1,
    titleKey: "x",
    category: "tickets",
    icon: "list",
    size: { default: [3, 2], min: [2, 2], max: [4, 3] },
    settingsSchema: null,
    dataEndpoint: null,
    refreshOn: [],
    permission: null,
    component: () => null,
    ...over,
  };
}

describe("defineCoreWidget", () => {
  it("maps the spec shape onto the shared WidgetDefinition", () => {
    const def = defineCoreWidget(spec({ description: "dashboards.widgets.x.description" }));
    expect(def).toMatchObject({
      key: "core.x",
      owner: "core",
      version: 2,
      titleKey: "dashboards.widgets.x.title",
      descriptionKey: "dashboards.widgets.x.description",
      settingsSchema: { type: "object", properties: {} },
      dataEndpoint: "core.x",
      refreshOn: ["ticket_changed"],
      permission: null,
    });
    expect(def.ns).toBeUndefined();
    expect(defineCoreWidget(spec({ data: null })).dataEndpoint).toBeNull();
    expect(defineCoreWidget(spec({ permission: "stats" })).permission).toBe("stats");
  });

  it("registers a typed widget without a cast (tsc fixture)", () => {
    function Typed({ settings }: WidgetProps<{ limit: number }>) {
      return <span>{settings.limit}</span>;
    }
    const def = defineCoreWidget<{ limit: number }>({
      key: "core.typed-fixture",
      version: 1,
      title: "t",
      category: "other",
      icon: "list",
      size: { default: [3, 2], min: [2, 2], max: [4, 3] },
      settings: { type: "object", properties: { limit: { type: "integer", default: 3 } } },
      data: null,
      refreshOn: [],
      component: Typed,
      migrate: () => ({ limit: 1 }),
    });
    DASHBOARD_WIDGETS.clearForTests();
    DASHBOARD_WIDGETS.register(def, "core");
    expect(DASHBOARD_WIDGETS.get("core.typed-fixture")?.component).toBe(Typed);
    DASHBOARD_WIDGETS.clearForTests();
  });
});

describe("galleryGroupOf", () => {
  it("maps tickets and team to themselves and everything else to 'Für mich'", () => {
    expect(galleryGroupOf(anyDef({ category: "tickets" }))).toBe("tickets");
    expect(galleryGroupOf(anyDef({ category: "team" }))).toBe("team");
    expect(galleryGroupOf(anyDef({ category: "other" }))).toBe("me");
    expect(galleryGroupOf(anyDef({ category: "reports" }))).toBe("me");
    expect(galleryGroupOf(anyDef({ category: "ai" }))).toBe("me");
  });
});

describe("settingsWithDefaults", () => {
  const def = anyDef({
    version: 2,
    settingsSchema: { type: "object", properties: { limit: { type: "integer", default: 8, minimum: 1, maximum: 50 } } },
    migrate: (old: unknown) => ({ limit: (old as { max?: number }).max ?? 8 }),
  });

  it("migrates old versions, fills defaults and falls back on garbage", () => {
    expect(settingsWithDefaults(def, { max: 3 }, 1)).toEqual({ limit: 3 });
    expect(settingsWithDefaults(def, {}, 2)).toEqual({ limit: 8 });
    expect(settingsWithDefaults(def, "garbage", 2)).toEqual({ limit: 8 });
    expect(settingsWithDefaults(def, null, 2)).toEqual({ limit: 8 });
    expect(settingsWithDefaults(def, [1, 2], 2)).toEqual({ limit: 8 });
  });

  it("replaces values of the wrong type or out of range, keeps valid and unknown ones", () => {
    const schema = {
      type: "object",
      properties: {
        limit: { type: "integer", default: 8, minimum: 1, maximum: 50 },
        density: { type: "string", enum: ["compact", "comfortable"], default: "comfortable" },
        only: { type: "boolean", default: true },
        columns: { type: "array", items: { type: "string", enum: ["tn", "title"] }, default: ["tn"] },
        source: { type: "object", default: { kind: "preset", preset: "mine_open" } },
      },
    };
    const d = anyDef({ settingsSchema: schema });
    expect(
      settingsWithDefaults(d, { limit: 99, density: "huge", only: "yes", columns: ["tn", "evil"], source: "x", extra: 1 }, 1),
    ).toEqual({ limit: 8, density: "comfortable", only: true, columns: ["tn"], source: { kind: "preset", preset: "mine_open" }, extra: 1 });
    expect(settingsWithDefaults(d, { limit: 3, density: "compact", only: false, columns: ["title"], source: { kind: "preset", preset: "mine_new" } }, 1)).toEqual({
      limit: 3,
      density: "compact",
      only: false,
      columns: ["title"],
      source: { kind: "preset", preset: "mine_new" },
    });
  });

  it("never hands out the schema's own default objects", () => {
    const d = anyDef({ settingsSchema: { type: "object", properties: { columns: { type: "array", default: ["tn"] } } } });
    const a = settingsWithDefaults(d, {}, 1);
    (a.columns as string[]).push("title");
    expect(settingsWithDefaults(d, {}, 1)).toEqual({ columns: ["tn"] });
  });

  it("falls back to defaults when migrate throws, and copies settings when there is no schema", () => {
    const throwing = anyDef({
      version: 3,
      settingsSchema: { type: "object", properties: { limit: { type: "integer", default: 5 } } },
      migrate: () => {
        throw new Error("boom");
      },
    });
    expect(settingsWithDefaults(throwing, { limit: 2 }, 1)).toEqual({ limit: 5 });
    expect(settingsWithDefaults(anyDef(), { city: "Bonn" }, 1)).toEqual({ city: "Bonn" });
  });
});

describe("canUseWidget", () => {
  const agent = { is_admin: false, can_view_reports: false };
  const admin = { is_admin: true, can_view_reports: true };

  it("null is for everyone; admin and stats follow the user flags; unknown fails closed", () => {
    expect(canUseWidget(anyDef({ permission: null }), agent)).toBe(true);
    expect(canUseWidget(anyDef({ permission: "stats" }), agent)).toBe(false);
    expect(canUseWidget(anyDef({ permission: "stats" }), { ...agent, can_view_reports: true })).toBe(true);
    expect(canUseWidget(anyDef({ permission: "admin" }), agent)).toBe(false);
    expect(canUseWidget(anyDef({ permission: "admin" }), admin)).toBe(true);
    expect(canUseWidget(anyDef({ permission: "superuser" }), admin)).toBe(false);
  });
});

describe("COLUMN_KEYS", () => {
  it("lists the backend's ticket columns in its order", () => {
    expect(COLUMN_KEYS).toEqual(["tn", "title", "customer", "state", "due", "queue", "owner", "priority", "changed", "created"]);
  });
});

describe("useWidgetData", () => {
  it("reports status 'none' outside a provider instead of throwing", () => {
    const { result } = renderHook(() => useWidgetData());
    expect(result.current.status).toBe("none");
    expect(result.current.data).toBeUndefined();
    expect(() => result.current.refetch()).not.toThrow();
  });
});
