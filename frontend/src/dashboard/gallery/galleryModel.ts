/**
 * Pure model of the widget gallery: grouping, search, per-widget glyphs and the conversion of a
 * widget definition into what the edit reducer's `add` action needs.
 */
import type { TFunction } from "i18next";
import type { WidgetDefinition } from "@/plugins/dashboardWidgets";
import { galleryGroupOf, settingsWithDefaults } from "../definitions";
import type { WidgetLike } from "../layout/session";

/** MIME type of the drag payload; the data is the widget key. */
export const WIDGET_DRAG_TYPE = "application/x-tiqora-widget";

export type GalleryGroupId = "tickets" | "me" | "team";
export type GalleryGroup = { id: GalleryGroupId; items: WidgetDefinition[] };

const ORDER: GalleryGroupId[] = ["tickets", "me", "team"];

/** The mockup's glyph per core widget; plugin widgets get a neutral diamond. */
export const GLYPHS: Record<string, string> = {
  "core.ticket-list": "≡",
  "core.counter": "12",
  "core.escalations": "!",
  "core.queue-overview": "Q",
  "core.reminders": "◷",
  "core.calendar-agenda": "31",
  "core.recent-views": "↺",
  "core.team-presence": "●",
  "core.notes": "✎",
  "core.links": "↗",
};

export type GlyphTone = "accent" | "green" | "danger" | "amber" | "purple" | "muted";

const TONES: Record<string, GlyphTone> = {
  "core.ticket-list": "accent",
  "core.counter": "green",
  "core.escalations": "danger",
  "core.queue-overview": "accent",
  "core.reminders": "amber",
  "core.calendar-agenda": "purple",
  "core.recent-views": "muted",
  "core.team-presence": "green",
  "core.notes": "amber",
  "core.links": "muted",
};

export function glyphOf(def: Pick<WidgetDefinition, "key">): string {
  return GLYPHS[def.key] ?? "◇";
}

export function toneOf(def: Pick<WidgetDefinition, "key">): GlyphTone {
  return TONES[def.key] ?? "muted";
}

/** Translated title and description of a definition (plugin widgets resolve in their namespace). */
export function widgetTexts(def: WidgetDefinition, t: TFunction): { title: string; description: string } {
  return {
    title: t(def.titleKey, { ns: def.ns, defaultValue: def.key }),
    description: def.descriptionKey ? t(def.descriptionKey, { ns: def.ns, defaultValue: "" }) : "",
  };
}

/**
 * Groups in the fixed order Tickets / Für mich / Team, items sorted by translated title, empty
 * groups omitted. `query` matches title and description case-insensitively; every
 * whitespace-separated term must match somewhere.
 */
export function buildGallery(
  defs: WidgetDefinition[],
  query: string,
  t: TFunction,
  canUse: (def: WidgetDefinition) => boolean,
  locale?: string,
): GalleryGroup[] {
  const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
  const groups = new Map<GalleryGroupId, { def: WidgetDefinition; title: string }[]>(ORDER.map((id) => [id, []]));
  for (const def of defs) {
    if (!canUse(def)) continue;
    const { title, description } = widgetTexts(def, t);
    const haystack = `${title}\n${description}`.toLowerCase();
    if (!terms.every((term) => haystack.includes(term))) continue;
    groups.get(galleryGroupOf(def))!.push({ def, title });
  }
  return ORDER.flatMap((id) => {
    const items = groups.get(id)!;
    if (items.length === 0) return [];
    items.sort((a, b) => a.title.localeCompare(b.title, locale));
    return [{ id, items: items.map((i) => i.def) }];
  });
}

/** What the reducer's `add` needs of a definition (settings start from the schema's defaults). */
export function widgetLikeOf(def: WidgetDefinition): WidgetLike {
  return {
    key: def.key,
    version: def.version,
    size: def.size,
    settingsDefaults: settingsWithDefaults(def, {}, def.version),
  };
}
