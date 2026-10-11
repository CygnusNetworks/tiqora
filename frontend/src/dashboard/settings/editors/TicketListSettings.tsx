import { useId } from "react";
import { useTranslation } from "react-i18next";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { cn } from "@/lib/cn";
import { COLUMN_KEYS, TICKET_PRESETS, type ColumnKey } from "../../definitions";
import { presetOf } from "../../widgets/presets";
import type { SettingsEditorProps } from "../registry";

/** The eight column chips of the settings mockup; stored columns outside it stay visible. */
const CHIP_COLUMNS: readonly ColumnKey[] = ["tn", "title", "customer", "state", "due", "queue", "owner", "priority"];

const SORTS = [
  { id: "deadline:asc", sort: "deadline", order: "asc", key: "deadlineAsc" },
  { id: "changed:desc", sort: "changed", order: "desc", key: "changedDesc" },
  { id: "priority:desc", sort: "priority", order: "desc", key: "priorityDesc" },
  { id: "age:asc", sort: "age", order: "asc", key: "ageAsc" },
  { id: "age:desc", sort: "age", order: "desc", key: "ageDesc" },
] as const;

const FIELD = "w-full rounded border border-hairline bg-surface px-2 py-1 text-sm text-ink focus:outline-none focus:ring-1 focus:ring-accent";
const LEGEND = "mb-2 text-xs font-medium text-muted";

/** Ticket list: which tickets, columns, sorting, rows and density (Task 17 adds filters and saved searches). */
export function TicketListSettingsEditor({ value, onChange, errors, canEditSources }: SettingsEditorProps) {
  const { t } = useTranslation();
  const uid = useId();
  const set = (p: Record<string, unknown>) => onChange({ ...value, ...p });

  const preset = presetOf(value.source) ?? "mine_open";
  const columns = (Array.isArray(value.columns) ? value.columns : []).filter((c): c is ColumnKey =>
    (COLUMN_KEYS as readonly unknown[]).includes(c),
  );
  const chips = [...CHIP_COLUMNS, ...columns.filter((c) => !CHIP_COLUMNS.includes(c))];
  const toggle = (c: ColumnKey, on: boolean) => {
    if (on) set({ columns: columns.includes(c) ? columns : [...columns, c] });
    else if (columns.length > 1) set({ columns: columns.filter((x) => x !== c) });
  };

  const combo = `${String(value.sort)}:${String(value.order)}`;
  const known = SORTS.some((s) => s.id === combo);
  const limit = typeof value.limit === "number" ? value.limit : "";
  const limitErr = errors.limit;
  const density = value.density === "compact" ? "compact" : "comfortable";

  return (
    <div className="space-y-5">
      <fieldset className="min-w-0">
        <legend className={LEGEND}>{t("dashboards.settings.ticketList.which")}</legend>
        <label className="flex items-start gap-3 rounded-lg border border-accent bg-accent/10 p-3 text-sm">
          <input type="radio" name={`${uid}-src`} checked readOnly className="mt-1" />
          <span className="min-w-0 flex-1">
            <span className="block font-semibold text-ink">{t("dashboards.settings.ticketList.savedSearch")}</span>
            <span className="mt-0.5 block text-xs text-muted">{t("dashboards.settings.ticketList.savedSearchHint")}</span>
            <select
              aria-label={t("dashboards.settings.ticketList.savedSearch")}
              value={preset}
              disabled={!canEditSources}
              onChange={(e) => set({ source: { kind: "preset", preset: e.target.value } })}
              className={cn(FIELD, "mt-2")}
            >
              <optgroup label={t("dashboards.settings.ticketList.standard")}>
                {TICKET_PRESETS.map((p) => (
                  <option key={p} value={p}>
                    {t(`dashboards.presets.${p}.counter`)}
                  </option>
                ))}
              </optgroup>
            </select>
          </span>
        </label>
      </fieldset>

      <fieldset className="min-w-0">
        <legend className={LEGEND}>{t("dashboards.settings.ticketList.columns")}</legend>
        <div className="flex flex-wrap gap-2">
          {chips.map((c) => {
            const on = columns.includes(c);
            const last = on && columns.length === 1;
            return (
              <label
                key={c}
                className={cn(
                  "flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm text-ink",
                  on ? "border-accent bg-accent/10" : "border-hairline",
                  last && "opacity-70",
                )}
              >
                <input type="checkbox" checked={on} disabled={last} onChange={(e) => toggle(c, e.target.checked)} />
                {c === "title" ? t("dashboards.settings.ticketList.columnTitle") : t(`dashboards.columns.${c}`)}
              </label>
            );
          })}
        </div>
        {errors.columns && (
          <p role="alert" className="mt-1 text-xs text-danger">
            {errors.columns}
          </p>
        )}
      </fieldset>

      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor={`${uid}-sort`} className="mb-1 block text-xs font-medium text-muted">
            {t("dashboards.settings.ticketList.sort")}
          </label>
          <select
            id={`${uid}-sort`}
            value={known ? combo : "other"}
            aria-invalid={errors.sort ? true : undefined}
            onChange={(e) => {
              const s = SORTS.find((x) => x.id === e.target.value);
              if (s) set({ sort: s.sort, order: s.order });
            }}
            className={FIELD}
          >
            {!known && (
              <option value="other" disabled>
                {t("dashboards.settings.ticketList.sortOther")}
              </option>
            )}
            {SORTS.map((s) => (
              <option key={s.id} value={s.id}>
                {t(`dashboards.settings.ticketList.sorts.${s.key}`)}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor={`${uid}-limit`} className="mb-1 block text-xs font-medium text-muted">
            {t("dashboards.settings.ticketList.rows")}
          </label>
          <input
            id={`${uid}-limit`}
            type="number"
            min={1}
            max={50}
            step={1}
            value={limit}
            aria-invalid={limitErr ? true : undefined}
            aria-describedby={limitErr ? `${uid}-limit-err` : undefined}
            onChange={(e) => {
              const raw = e.target.value;
              if (raw === "") return set({ limit: null });
              const n = Math.trunc(Number(raw));
              set({ limit: Number.isFinite(n) ? Math.min(50, Math.max(1, n)) : null });
            }}
            className={cn(FIELD, limitErr && "border-danger")}
          />
          {limitErr && (
            <p id={`${uid}-limit-err`} role="alert" className="mt-1 text-xs text-danger">
              {limitErr}
            </p>
          )}
        </div>
      </div>

      <fieldset className="min-w-0">
        <legend className={LEGEND}>{t("dashboards.settings.ticketList.density")}</legend>
        <SegmentedControl
          aria-label={t("dashboards.settings.ticketList.density")}
          value={density}
          onChange={(v) => set({ density: v })}
          items={[
            { value: "compact", label: t("dashboards.settings.ticketList.compact") },
            { value: "comfortable", label: t("dashboards.settings.ticketList.comfortable") },
          ]}
        />
      </fieldset>
    </div>
  );
}
