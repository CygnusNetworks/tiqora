import { beforeAll, beforeEach, describe, expect, it } from "vitest";
import i18n from "@/i18n";
import { DASHBOARD_WIDGETS, defineWidget, type WidgetDefinition } from "@/plugins/dashboardWidgets";
import { canUseWidget, settingsWithDefaults } from "../definitions";
import { registerCoreWidgets } from "../widgets";
import { buildGallery, glyphOf, widgetLikeOf } from "./galleryModel";

const t = i18n.t.bind(i18n);
const all = () => true;
const keysOf = (g: ReturnType<typeof buildGallery>) => g.flatMap((x) => x.items.map((d) => d.key));

function pluginReports(): WidgetDefinition {
  i18n.addResourceBundle("de", "plugin.org.example", { chart: "Umsatzdiagramm", chartDesc: "Zeigt den Umsatz je Woche" }, true, true);
  return defineWidget({
    key: "org.example.chart",
    owner: "org.example",
    version: 1,
    titleKey: "chart",
    descriptionKey: "chartDesc",
    ns: "plugin.org.example",
    category: "reports",
    icon: "chart",
    size: { default: [4, 3], min: [2, 2], max: [12, 8] },
    settingsSchema: null,
    dataEndpoint: null,
    refreshOn: [],
    permission: null,
    component: () => null,
  });
}

let core: WidgetDefinition[];

beforeAll(async () => {
  await i18n.changeLanguage("de");
});
beforeEach(() => {
  registerCoreWidgets();
  core = DASHBOARD_WIDGETS.list().filter((d) => d.key.startsWith("core."));
});

describe("buildGallery", () => {
  it("groups appear as Tickets, Für mich, Team in that order; plugin widgets of category 'reports' land in Für mich", () => {
    const g = buildGallery([...core, pluginReports()], "", t, all);
    expect(g.map((x) => x.id)).toEqual(["tickets", "me", "team"]);
    expect(g[1].items.map((d) => d.key)).toContain("org.example.chart");
    expect(g[0].items.map((d) => d.key).sort()).toEqual(
      ["core.counter", "core.escalations", "core.queue-overview", "core.ticket-list"],
    );
  });

  it("items are sorted by translated title", () => {
    const titles = buildGallery(core, "", t, all)[0].items.map((d) => t(d.titleKey));
    expect(titles).toEqual([...titles].sort((a, b) => a.localeCompare(b)));
  });

  it("search matches title and description, case-insensitive, every term; empty groups vanish", () => {
    expect(keysOf(buildGallery(core, "eskal", t, all))).toEqual(["core.escalations"]);
    expect(keysOf(buildGallery(core, "NOTIZ team", t, all))).toEqual(["core.notes"]);
    expect(buildGallery(core, "eskal", t, all).map((g) => g.id)).toEqual(["tickets"]);
    expect(buildGallery(core, "zzz", t, all)).toEqual([]);
    expect(keysOf(buildGallery([pluginReports()], "WOCHE umsatz", t, all))).toEqual(["org.example.chart"]);
  });

  it("widgets the user may not use are not offered (real predicate, two users)", () => {
    const open = { ...core[0], key: "core.open", permission: null };
    const stats = { ...core[0], key: "core.stats", permission: "stats" };
    const offered = (can_view_reports: boolean) =>
      keysOf(buildGallery([open, stats], "", t, (d) => canUseWidget(d, { is_admin: false, can_view_reports })));
    expect(offered(false)).toEqual(["core.open"]);
    expect(offered(true).sort()).toEqual(["core.open", "core.stats"]);
  });
});

describe("widgetLikeOf / glyphOf", () => {
  it("carries the size and the schema defaults; plugin widgets get a neutral glyph", () => {
    const def = DASHBOARD_WIDGETS.get("core.ticket-list")!;
    const like = widgetLikeOf(def);
    expect(like).toMatchObject({ key: "core.ticket-list", version: def.version, size: def.size });
    expect(like.settingsDefaults).toEqual(settingsWithDefaults(def, {}, def.version));
    expect(glyphOf(def)).toBe("≡");
    expect(glyphOf({ key: "org.example.chart" })).toBe("◇");
  });
});
