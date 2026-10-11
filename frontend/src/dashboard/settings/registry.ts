/**
 * Which settings UI a widget type gets: the four bespoke core editors (ticket list, counter, links, notes), or a `SchemaForm` over the
 * definition's `settingsSchema` (every other core widget and every plugin widget).
 */
import type { ComponentType } from "react";
import type { JsonSchema } from "@/components/fields/jsonSchema";
import type { WidgetDefinition } from "@/plugins/dashboardWidgets";
import { CounterSettingsEditor } from "./editors/CounterSettings";
import { LinksSettingsEditor } from "./editors/LinksSettings";
import { NotesSettingsEditor } from "./editors/NotesSettings";
import { TicketListSettingsEditor } from "./editors/TicketListSettings";

export interface SettingsEditorProps {
  value: Record<string, unknown>;
  onChange(next: Record<string, unknown>): void;
  /** Field errors keyed by the field path inside the settings (`limit`, `items.0.url`). */
  errors: Record<string, string>;
  /** Focus the source section on mount (opened from a tile's "Suche wählen"). */
  focusSource?: boolean;
}

export const CORE_SETTINGS_EDITORS: Record<string, ComponentType<SettingsEditorProps>> = {
  "core.ticket-list": TicketListSettingsEditor,
  "core.counter": CounterSettingsEditor,
  "core.links": LinksSettingsEditor,
  "core.notes": NotesSettingsEditor,
};

/** A generated form over (a view of) the stored settings. */
export interface FormAdapter {
  schema: JsonSchema;
  /** Prefix of the field texts (`<prefix>.fields.<key>.label|help|options.<value>`). */
  i18nPrefix: string;
  toForm(settings: Record<string, unknown>): Record<string, unknown>;
  fromForm(form: Record<string, unknown>, prev: Record<string, unknown>): Record<string, unknown>;
}

const CORE_FORM_NAMES: Record<string, string> = {
  "core.queue-overview": "queueOverview",
  "core.escalations": "escalations",
  "core.reminders": "reminders",
  "core.recent-views": "recentViews",
  "core.calendar-agenda": "calendarAgenda",
  "core.team-presence": "teamPresence",
};

const identity = {
  toForm: (s: Record<string, unknown>) => s,
  fromForm: (f: Record<string, unknown>) => f,
};

/** The generated form of a widget type, or null when it has no settings schema (title only). */
export function formAdapterFor(def: Pick<WidgetDefinition, "key" | "owner" | "ns" | "settingsSchema">): FormAdapter | null {
  if (!def.settingsSchema) return null;
  const schema = def.settingsSchema as JsonSchema;
  if (def.owner === "core") {
    const name = CORE_FORM_NAMES[def.key];
    return name ? { ...identity, schema, i18nPrefix: `dashboards.settings.forms.${name}` } : null;
  }
  // Plugin widgets: labels come from the plugin's own namespace (`<ns>:widget.<key>.fields.<field>.label`),
  // falling back to the schema's titles.
  const local = def.key.startsWith(`${def.owner}.`) ? def.key.slice(def.owner.length + 1) : def.key;
  return { ...identity, schema, i18nPrefix: `${def.ns ?? `plugin.${def.owner}`}:widget.${local}` };
}
