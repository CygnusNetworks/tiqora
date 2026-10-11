import { useId, useMemo, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { fieldsFromTicketCatalog } from "@/components/filters/filterFields";
import { FilterBuilder } from "@/components/filters/FilterBuilder";
import { SAVED_SEARCHES_KEY } from "@/components/searches/savedSearchQueries";
import { cn } from "@/lib/cn";
import { savedSearchesApi, type TicketFilter } from "@/lib/savedSearchesApi";
import { TICKET_PRESETS, type TicketPreset } from "../../definitions";
import { presetOf, savedSearchIdOf } from "../../widgets/presets";

export const FIELD =
  "w-full rounded border border-hairline bg-surface px-2 py-1 text-sm text-ink focus:outline-none focus:ring-1 focus:ring-accent";
const LEGEND = "mb-2 text-xs font-medium text-muted";
const CARD = "flex items-start gap-3 rounded-lg border p-3 text-sm";

const EMPTY_FILTER: TicketFilter = { version: 1 };

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

/** The `filter` of an own-filter source, whatever shape is stored. */
function filterOf(source: unknown): TicketFilter | null {
  if (!isObj(source) || source.kind !== "filter") return null;
  return isObj(source.filter) ? (source.filter as unknown as TicketFilter) : EMPTY_FILTER;
}

/**
 * "Which tickets?" of ticket lists and counters: a saved search (the built-in views and the
 * searches the user can see) or an own filter built right here. Shared by both editors.
 * Server messages for `source.*` show under the card they belong to.
 */
export function SourceFields({
  value,
  onChange,
  errors,
  canEditSources,
}: {
  value: Record<string, unknown>;
  onChange(next: Record<string, unknown>): void;
  errors: Record<string, string>;
  canEditSources: boolean;
}) {
  const { t } = useTranslation();
  const uid = useId();
  const source = value.source;
  const filter = filterOf(source);
  const ownFilter = filter !== null;
  const preset = presetOf(source);
  const searchId = savedSearchIdOf(source);
  const set = (next: unknown) => onChange({ ...value, source: next });

  // What the other card had, so switching back and forth loses nothing.
  const lastFilter = useRef<TicketFilter>(filter ?? EMPTY_FILTER);
  const lastSearch = useRef<unknown>(preset || searchId ? source : { kind: "preset", preset: "mine_open" });
  if (filter) lastFilter.current = filter;
  else if (preset || searchId) lastSearch.current = source;

  const searchesQ = useQuery({
    queryKey: [...SAVED_SEARCHES_KEY, "list"],
    queryFn: ({ signal }) => savedSearchesApi.list(signal),
    staleTime: 30_000,
  });
  const fieldsQ = useQuery({
    queryKey: ["ticket-filters", "fields"],
    queryFn: ({ signal }) => savedSearchesApi.ticketFilterFields(signal),
    staleTime: 5 * 60_000,
    enabled: ownFilter,
  });
  const fields = useMemo(() => fieldsFromTicketCatalog(fieldsQ.data ?? [], t), [fieldsQ.data, t]);

  const searches = searchesQ.data ?? [];
  const mine = searches.filter((s) => s.is_owner);
  const shared = searches.filter((s) => !s.is_owner);
  const known = searchId !== null && searches.some((s) => s.id === searchId);
  const selected = searchId !== null ? `search:${searchId}` : `preset:${preset ?? "mine_open"}`;

  const sourceErrors = Object.entries(errors).filter(([k]) => k === "source" || k.startsWith("source."));
  const errorsFor = (kind: "search" | "filter") =>
    sourceErrors
      .filter(([k]) => (kind === "search" ? k === "source.id" : k !== "source.id"))
      .map(([, m]) => m);

  const pick = (v: string) => {
    if (v.startsWith("search:")) set({ kind: "saved_search", id: Number(v.slice("search:".length)) });
    else set({ kind: "preset", preset: v.slice("preset:".length) });
  };

  return (
    <fieldset className="min-w-0 space-y-2">
      <legend className={LEGEND}>{t("dashboards.settings.ticketList.which")}</legend>

      <div className={cn(CARD, ownFilter ? "border-hairline" : "border-accent bg-accent/10")}>
        <input
          id={`${uid}-search-radio`}
          type="radio"
          name={`${uid}-src`}
          aria-label={t("dashboards.settings.ticketList.savedSearch")}
          checked={!ownFilter}
          disabled={!canEditSources}
          onChange={() => set(lastSearch.current)}
          className="mt-1"
        />
        <div className="min-w-0 flex-1">
          <label htmlFor={`${uid}-search-radio`} className="block font-semibold text-ink">
            {t("dashboards.settings.ticketList.savedSearch")}
          </label>
          <span className="mt-0.5 block text-xs text-muted">{t("dashboards.settings.ticketList.savedSearchHint")}</span>
          <select
            aria-label={t("dashboards.settings.source.pick")}
            value={ownFilter ? "" : selected}
            disabled={!canEditSources || ownFilter}
            aria-invalid={errorsFor("search").length > 0 ? true : undefined}
            onChange={(e) => pick(e.target.value)}
            className={cn(FIELD, "mt-2")}
          >
            {ownFilter && <option value="" />}
            <optgroup label={t("dashboards.settings.ticketList.standard")}>
              {TICKET_PRESETS.map((p: TicketPreset) => (
                <option key={p} value={`preset:${p}`}>
                  {t(`dashboards.presets.${p}.counter`)}
                </option>
              ))}
            </optgroup>
            {mine.length > 0 && (
              <optgroup label={t("dashboards.settings.source.mine")}>
                {mine.map((s) => (
                  <option key={s.id} value={`search:${s.id}`}>
                    {s.name}
                  </option>
                ))}
              </optgroup>
            )}
            {shared.length > 0 && (
              <optgroup label={t("dashboards.settings.source.shared")}>
                {shared.map((s) => (
                  <option key={s.id} value={`search:${s.id}`}>
                    {s.name}
                  </option>
                ))}
              </optgroup>
            )}
            {searchId !== null && !known && searchesQ.isSuccess && (
              <option value={`search:${searchId}`} disabled>
                {t("dashboards.settings.source.gone")}
              </option>
            )}
          </select>
          {errorsFor("search").map((m) => (
            <p key={m} role="alert" className="mt-1 text-xs text-danger">
              {m}
            </p>
          ))}
        </div>
      </div>

      <div className={cn(CARD, ownFilter ? "border-accent bg-accent/10" : "border-hairline")}>
        <input
          id={`${uid}-filter-radio`}
          type="radio"
          name={`${uid}-src`}
          aria-label={t("dashboards.settings.source.ownFilter")}
          checked={ownFilter}
          disabled={!canEditSources}
          onChange={() => set({ kind: "filter", filter: lastFilter.current })}
          className="mt-1"
        />
        <div className="min-w-0 flex-1">
          <label htmlFor={`${uid}-filter-radio`} className="block font-semibold text-ink">
            {t("dashboards.settings.source.ownFilter")}
          </label>
          <span className="mt-0.5 block text-xs text-muted">{t("dashboards.settings.source.ownFilterHint")}</span>
          {ownFilter && (
            <div className="mt-3">
              <FilterBuilder
                fields={fields}
                value={filter}
                disabled={!canEditSources || !fieldsQ.isSuccess}
                onChange={(next) => set({ kind: "filter", filter: next })}
              />
              {errorsFor("filter").map((m) => (
                <p key={m} role="alert" className="mt-2 text-xs text-danger">
                  {m}
                </p>
              ))}
            </div>
          )}
        </div>
      </div>
    </fieldset>
  );
}
