/**
 * Field lists for the filter builder: from the ticket filter catalog
 * (`GET /ticket-filters/fields`) and from a report source's dimensions and
 * filter-only fields (`GET /reporting/sources`). The operators always come from
 * the API; the frontend `OPS_BY_TYPE` is only the fallback for new conditions.
 */
import type { TFunction } from "i18next";
import type { FilterFieldOut } from "@/lib/savedSearchesApi";
import type { ReportSource } from "@/lib/reportsApi";
import { OPS_BY_TYPE, type FilterFieldDef, type FilterFieldType } from "./filterModel";

type SourceField = ReportSource["dimensions"][number];

const ATTRIBUTE_SOURCES = new Set(["queue", "state", "priority", "type", "service", "sla"]);
const USER_FIELDS = new Set(["owner_id", "responsible_id", "watched_by"]);
const DF_SOURCE_PREFIX = "DynamicField_";

function catalogSource(f: FilterFieldOut): string | null {
  if (USER_FIELDS.has(f.key)) return "user";
  const src = f.options_source;
  if (!src) return null;
  if (src.startsWith(DF_SOURCE_PREFIX)) return `df:${src.slice(DF_SOURCE_PREFIX.length)}`;
  return ATTRIBUTE_SOURCES.has(src) ? src : null;
}

export function fieldsFromTicketCatalog(fields: FilterFieldOut[], t: TFunction): FilterFieldDef[] {
  return fields.map((f) => ({
    key: f.key,
    label: f.label ?? (f.label_key ? t(f.label_key) : f.key),
    type: f.type,
    ops: f.ops,
    enumValues: f.enum_values.map((value) => ({
      value,
      label: t(`reports.values.${f.key}.${value}`, { defaultValue: value }),
    })),
    optionsSource: catalogSource(f),
  }));
}

function sourceOptions(f: SourceField): string | null {
  if (f.options_source && ATTRIBUTE_SOURCES.has(f.options_source)) return f.options_source;
  return f.labels === "user" ? "user" : null;
}

export function fieldsFromSource(source: ReportSource, t: TFunction): FilterFieldDef[] {
  const out: FilterFieldDef[] = [];
  const seen = new Set<string>();
  for (const f of [...source.dimensions, ...source.filter_fields]) {
    if (seen.has(f.key)) continue;
    const type = (f.filter_type ?? f.kind) as FilterFieldType;
    if (!(type in OPS_BY_TYPE)) continue;
    const ops = f.ops ?? [];
    if (ops.length === 0) continue; // not filterable
    seen.add(f.key);
    out.push({
      key: f.key,
      label: t(f.label_key),
      type,
      ops,
      enumValues: (f.enum_values ?? []).map((value) => ({
        value,
        label: f.i18n_values ? t(`${f.i18n_values}.${value}`, { defaultValue: value }) : value,
      })),
      optionsSource: sourceOptions(f),
    });
  }
  return out;
}
