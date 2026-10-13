import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useDebounced } from "@/components/fields/useDebounced";
import { Button } from "@/components/ui/Button";
import { SelectField } from "@/components/ui/SelectField";
import { cn } from "@/lib/cn";
import type { CatalogItem, CmdbClass, FilterField } from "@/lib/cmdbApi";
import { VALUELESS_OPS, type AttrFilterRow, type CiFilterState } from "./ciFilterModel";

const INPUT_CLS =
  "w-full rounded border border-hairline bg-surface px-2 py-1.5 text-sm text-ink focus:outline-none focus:ring-1 focus:ring-accent disabled:cursor-not-allowed disabled:opacity-60";

/** Operators the row builder offers (the field's own `ops` narrow it further). */
const BUILDER_OPS = ["eq", "neq", "contains", "starts_with", "lt", "lte", "gt", "gte", "before", "after", "is_empty", "is_not_empty"];
export const INCIDENT_FUNCTIONALITIES = ["operational", "warning", "incident"] as const;
export const SEARCH_DEBOUNCE_MS = 300;

function Chip({ active, onClick, children, testId }: { active: boolean; onClick: () => void; children: string; testId: string }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      data-testid={testId}
      className={cn(
        "rounded-full border px-2.5 py-1 text-xs font-medium transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent",
        active ? "border-accent bg-accent-dim text-accent" : "border-hairline bg-surface text-muted hover:text-ink",
      )}
    >
      {children}
    </button>
  );
}

const toggle = <T,>(list: T[], v: T): T[] => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

/**
 * Filter bar of the CMDB list: class, free text (debounced), deployment states, incident
 * functionalities and, for one selected class only, the attribute filter rows.
 */
export function CiFilterBar({
  state,
  onChange,
  classes,
  attrFields,
  deploymentItems,
}: {
  state: CiFilterState;
  onChange: (next: CiFilterState) => void;
  classes: CmdbClass[];
  /** `attr.*` filter fields of the selected class (empty without one). */
  attrFields: FilterField[];
  deploymentItems: CatalogItem[];
}) {
  const { t } = useTranslation();
  const [qText, setQText] = useState(state.q);
  const debounced = useDebounced(qText, SEARCH_DEBOUNCE_MS);

  useEffect(() => {
    if (debounced.trim() !== state.q.trim()) onChange({ ...state, q: debounced });
    // only a settled input drives the state
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced]);
  useEffect(() => {
    setQText((prev) => (prev.trim() === state.q.trim() ? prev : state.q));
  }, [state.q]);

  const hasClass = state.classId != null;
  const fieldLabel = (f: FilterField) => f.label ?? (f.label_key ? t(f.label_key) : f.key);

  const setRow = (i: number, patch: Partial<AttrFilterRow>) =>
    onChange({ ...state, attr: state.attr.map((r, j) => (j === i ? { ...r, ...patch } : r)) });

  const addRow = () => {
    const f = attrFields[0];
    if (!f) return;
    onChange({ ...state, attr: [...state.attr, { field: f.key, op: opsFor(f)[0] ?? "eq", value: "" }] });
  };

  const opsFor = (f: FilterField) => BUILDER_OPS.filter((o) => f.ops.includes(o));

  return (
    <div className="space-y-3" data-testid="ci-filter-bar">
      <div className="grid gap-3 sm:grid-cols-[14rem_1fr]">
        <SelectField<number>
          testId="ci-filter-class"
          aria-label={t("cmdb.list.class")}
          value={state.classId ?? 0}
          items={[{ value: 0, label: t("cmdb.list.allClasses") }, ...classes.map((c) => ({ value: c.id, label: c.name }))]}
          onChange={(v) => onChange({ ...state, classId: v === 0 ? null : v, attr: [] })}
        />
        <input
          type="search"
          className={INPUT_CLS}
          value={qText}
          onChange={(e) => setQText(e.target.value)}
          placeholder={t("cmdb.list.searchPlaceholder")}
          aria-label={t("cmdb.list.search")}
          data-testid="ci-filter-q"
        />
      </div>

      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label={t("cmdb.list.deploymentState")}>
          <span className="mr-1 text-xs text-muted">{t("cmdb.list.deploymentState")}</span>
          {deploymentItems.map((d) => (
            <Chip
              key={d.id}
              testId={`ci-filter-dep-${d.id}`}
              active={state.deployment.includes(d.id)}
              onClick={() => onChange({ ...state, deployment: toggle(state.deployment, d.id) })}
            >
              {d.name}
            </Chip>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label={t("cmdb.list.incidentState")}>
          <span className="mr-1 text-xs text-muted">{t("cmdb.list.incidentState")}</span>
          {INCIDENT_FUNCTIONALITIES.map((f) => (
            <Chip
              key={f}
              testId={`ci-filter-inc-${f}`}
              active={state.incident.includes(f)}
              onClick={() => onChange({ ...state, incident: toggle(state.incident, f) })}
            >
              {t(`cmdb.state.${f}`)}
            </Chip>
          ))}
        </div>
      </div>

      <div className="space-y-2" data-testid="ci-attr-filter">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-medium text-ink">{t("cmdb.list.attrFilter")}</span>
          <Button
            size="sm"
            variant="secondary"
            onClick={addRow}
            disabled={!hasClass || attrFields.length === 0}
            data-testid="ci-attr-add"
          >
            {t("cmdb.list.attrAdd")}
          </Button>
          {!hasClass && <span className="text-xs text-muted">{t("cmdb.list.attrNeedsClass")}</span>}
          {hasClass && attrFields.length === 0 && <span className="text-xs text-muted">{t("cmdb.list.attrNone")}</span>}
        </div>
        {hasClass &&
          state.attr.map((row, i) => {
            const field = attrFields.find((f) => f.key === row.field);
            const valueless = VALUELESS_OPS.has(row.op);
            return (
              <div key={i} className="grid gap-2 sm:grid-cols-[1fr_9rem_1fr_auto]" data-testid={`ci-attr-row-${i}`}>
                <SelectField<string>
                  testId={`ci-attr-field-${i}`}
                  aria-label={t("cmdb.list.attrField")}
                  value={row.field}
                  items={attrFields.map((f) => ({ value: f.key, label: fieldLabel(f) }))}
                  onChange={(v) => {
                    const nf = attrFields.find((f) => f.key === v);
                    setRow(i, { field: v, op: nf ? (opsFor(nf)[0] ?? "eq") : "eq", value: "" });
                  }}
                />
                <SelectField<string>
                  testId={`ci-attr-op-${i}`}
                  aria-label={t("cmdb.list.attrOp")}
                  value={row.op}
                  items={(field ? opsFor(field) : ["eq"]).map((o) => ({ value: o, label: t(`cmdb.list.ops.${o}`) }))}
                  onChange={(v) => setRow(i, { op: v, value: VALUELESS_OPS.has(v) ? undefined : (row.value ?? "") })}
                />
                {valueless ? (
                  <span />
                ) : field?.type === "enum" ? (
                  <SelectField<string>
                    testId={`ci-attr-value-${i}`}
                    aria-label={t("cmdb.list.attrValue")}
                    value={typeof row.value === "string" ? row.value : null}
                    placeholder={t("cmdb.form.select")}
                    items={field.enum_values.map((v) => ({ value: v, label: v }))}
                    onChange={(v) => setRow(i, { value: v })}
                  />
                ) : (
                  <input
                    className={INPUT_CLS}
                    type={field?.type === "int" ? "number" : field?.type === "datetime" ? "date" : "text"}
                    value={row.value === undefined || row.value === null ? "" : String(row.value)}
                    aria-label={t("cmdb.list.attrValue")}
                    data-testid={`ci-attr-value-${i}`}
                    onChange={(e) => {
                      const raw = e.target.value;
                      setRow(i, { value: field?.type === "int" && raw !== "" && Number.isFinite(Number(raw)) ? Number(raw) : raw });
                    }}
                  />
                )}
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => onChange({ ...state, attr: state.attr.filter((_, j) => j !== i) })}
                  aria-label={t("cmdb.list.attrRemove")}
                  data-testid={`ci-attr-remove-${i}`}
                >
                  ✕
                </Button>
              </div>
            );
          })}
      </div>
    </div>
  );
}
