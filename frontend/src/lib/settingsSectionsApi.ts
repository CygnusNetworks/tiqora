/**
 * Admin settings sections (`backend/src/tiqora/api/v1/admin/settings_sections.py`).
 * Hand-written wrappers like `./phoneApi.ts`.
 */
import type { Schemas } from "@tiqora/api-client";
import { secretKeys } from "@/components/fields/jsonSchema";
import type { FieldErrors, FieldValue, FieldValues } from "@/components/fields/types";
import { ApiError, api } from "./api";

export type SettingsSection = Schemas["SettingsSectionOut"];
export type SettingsSectionSummary = Schemas["SettingsSectionSummaryOut"];

const path = (key: string) => `/api/v1/admin/settings-sections/${encodeURIComponent(key)}`;

export const settingsSectionsApi = {
  list(signal?: AbortSignal) {
    return api.request<SettingsSectionSummary[]>("GET", "/api/v1/admin/settings-sections", { signal });
  },
  get(key: string, signal?: AbortSignal) {
    return api.request<SettingsSection>("GET", path(key), { signal });
  },
  put(key: string, values: Record<string, unknown>) {
    return api.request<SettingsSection>("PUT", path(key), { body: { values } });
  },
};

/** Field messages of a 422 (`detail.errors`); empty for any other error. */
export function settingsFieldErrors(err: unknown): FieldErrors {
  if (!(err instanceof ApiError) || err.status !== 422) return {};
  const body = err.detail as { detail?: { errors?: unknown } } | null;
  const errors = body && typeof body === "object" ? body.detail?.errors : null;
  if (!errors || typeof errors !== "object") return {};
  const out: FieldErrors = {};
  for (const [k, v] of Object.entries(errors as Record<string, unknown>)) {
    if (typeof v === "string") out[k] = v;
  }
  return out;
}

/** Form values for a loaded section; secrets start untouched (`null`), never the server's mask. */
export function initialSectionValues(section: SettingsSection): FieldValues {
  const out = { ...(section.values as FieldValues) };
  for (const key of secretKeys(section.json_schema)) out[key] = null;
  return out;
}

/** Only what the admin changed; an untouched secret (`null`) is omitted, `""` clears it. */
export function changedSectionValues(section: SettingsSection, draft: FieldValues): Record<string, unknown> {
  const initial = initialSectionValues(section);
  const secrets = new Set(secretKeys(section.json_schema));
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(draft) as [string, FieldValue][]) {
    if (secrets.has(key)) {
      if (value !== null) out[key] = value;
    } else if (JSON.stringify(value) !== JSON.stringify(initial[key])) {
      out[key] = value;
    }
  }
  return out;
}
