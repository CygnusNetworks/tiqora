import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { Button } from "@/components/ui/Button";
import { SelectField } from "@/components/ui/SelectField";
import { cn } from "@/lib/cn";
import type { TicketFilter } from "@/lib/savedSearchesApi";
import {
  conditionError,
  defaultOp,
  emptyGroup,
  emptyValueFor,
  fromFilter,
  newId,
  toFilter,
  valueShape,
  type EditCondition,
  type EditGroup,
  type FilterFieldDef,
} from "./filterModel";
import { FilterValueInput } from "./FilterValueInput";

const selectCls =
  "min-w-0 rounded border border-hairline bg-surface px-2 py-1.5 text-sm text-ink focus:outline-none focus:ring-1 focus:ring-accent disabled:cursor-not-allowed disabled:opacity-60";

function wireKey(f: TicketFilter | null | undefined): string {
  return JSON.stringify(f?.where ?? null);
}

function mapGroup(group: EditGroup, id: string, fn: (g: EditGroup) => EditGroup): EditGroup {
  if (group.id === id) return fn(group);
  return {
    ...group,
    children: group.children.map((c) => (c.kind === "group" ? mapGroup(c, id, fn) : c)),
  };
}

function valueText(value: unknown, t: TFunction): string {
  if (Array.isArray(value)) return value.map(String).join(", ");
  if (value && typeof value === "object") {
    const v = value as Record<string, unknown>;
    if ("amount" in v) return `${v.amount} ${t(`filters.units.${v.unit}`, { defaultValue: String(v.unit) })}`;
    return `${v.from ?? ""} – ${v.to ?? ""}`;
  }
  return value === undefined || value === null ? "" : String(value);
}

/** Plain-language reading of a filter the editor cannot show. */
function summarize(
  node: unknown,
  labels: Map<string, string>,
  t: TFunction,
  top = true,
): string {
  if (!node || typeof node !== "object") return "";
  const n = node as Record<string, unknown>;
  if (typeof n.field === "string") {
    const op = t(`filters.ops.${n.op}`, { defaultValue: String(n.op) });
    return [labels.get(n.field) ?? n.field, op, valueText(n.value, t)].filter(Boolean).join(" ");
  }
  if (n.not) return `${t("filters.not")}: ${summarize(n.not, labels, t, false)}`;
  const mode = Array.isArray(n.all) ? "all" : "any";
  const parts = ((n[mode] as unknown[]) ?? []).map((c) => summarize(c, labels, t, false));
  const text = parts.join(mode === "all" ? " · " : " | ");
  return top ? text : `(${text})`;
}

/**
 * Edits a `TicketFilter` as "all / any of these conditions" with one level of groups.
 * Keeps its own edit tree (typing needs it) and takes over `value` again when the parent
 * passes a different filter. Every change is reported through `onChange`, valid or not;
 * run `ticketFilterErrors(filter, fields)` on the result before saving (empty map = valid).
 */
export function FilterBuilder({
  fields,
  value,
  onChange,
  testId = "filter-builder",
  disabled,
}: {
  fields: FilterFieldDef[];
  value: TicketFilter | null;
  onChange: (next: TicketFilter) => void;
  testId?: string;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const uid = useId();
  const [model, setModel] = useState(() => fromFilter(value));
  const lastProp = useRef(wireKey(value));
  const rootRef = useRef<HTMLDivElement | null>(null);
  const focusAfter = useRef<string | null>(null);
  const [touched, setTouched] = useState<ReadonlySet<string>>(new Set());

  // The parent handed over a different filter than the one it last got from us.
  const incoming = wireKey(value);
  if (incoming !== lastProp.current) {
    lastProp.current = incoming;
    if (incoming !== wireKey(toFilter(model.root))) {
      setModel(fromFilter(value));
      setTouched(new Set());
    }
  }

  // After a removal the focused button is gone: move focus to a neighbour.
  useEffect(() => {
    const id = focusAfter.current;
    if (!id) return;
    focusAfter.current = null;
    rootRef.current?.querySelector<HTMLElement>(`[data-testid="${id}"]`)?.focus();
  });

  const byKey = useMemo(() => new Map(fields.map((f) => [f.key, f])), [fields]);
  const labels = useMemo(() => new Map(fields.map((f) => [f.key, f.label])), [fields]);

  const commit = (root: EditGroup) => {
    setModel({ root, editable: true });
    onChange(toFilter(root));
  };
  const touch = (id: string) => setTouched((s) => (s.has(id) ? s : new Set(s).add(id)));

  if (!model.editable) {
    return (
      <div className="space-y-2 rounded-lg border border-hairline bg-surface p-3" data-testid={testId}>
        <p className="text-sm text-ink" data-testid={`${testId}-too-complex`}>
          {t("filters.tooComplex")}
        </p>
        <p className="text-[13px] text-muted" data-testid={`${testId}-summary`}>
          {summarize(value?.where, labels, t)}
        </p>
        <Button
          size="sm"
          disabled={disabled}
          data-testid={`${testId}-reset`}
          onClick={() => {
            const empty = fromFilter(null);
            setModel(empty);
            onChange(toFilter(empty.root));
          }}
        >
          {t("filters.reset")}
        </Button>
      </div>
    );
  }

  const newCondition = (): EditCondition => {
    const field = fields[0];
    const op = defaultOp(field.ops);
    return { kind: "condition", id: newId(), field: field.key, op, value: emptyValueFor(op, field.type) };
  };

  const patchGroup = (id: string, fn: (g: EditGroup) => EditGroup) =>
    commit(mapGroup(model.root, id, fn));
  const patchCondition = (groupId: string, condId: string, fn: (c: EditCondition) => EditCondition) =>
    patchGroup(groupId, (g) => ({
      ...g,
      children: g.children.map((c) => (c.kind === "condition" && c.id === condId ? fn(c) : c)),
    }));

  const renderCondition = (group: EditGroup, c: EditCondition, i: number, prefix: string) => {
    const def = byKey.get(c.field);
    const rowId = `${prefix}-row-${i}`;
    const error = touched.has(c.id) ? conditionError(c, def) : null;
    const errorId = `${uid}-${c.id}-err`;
    const fieldItems = def ? fields : [...fields, { key: c.field, label: c.field } as FilterFieldDef];
    const ops = def?.ops ?? [c.op];
    const label = def?.label ?? c.field;
    return (
      <li key={c.id} className="space-y-1" onBlur={() => touch(c.id)}>
        <div className="flex flex-wrap items-start gap-2">
          <div className="w-44 max-w-full">
            <SelectField
              testId={`${rowId}-field`}
              aria-label={t("filters.field")}
              disabled={disabled}
              items={fieldItems.map((f) => ({ value: f.key, label: f.label }))}
              value={c.field}
              onChange={(key) => {
                if (key === c.field) return;
                const next = byKey.get(key);
                if (!next) return;
                const op = defaultOp(next.ops);
                touch(c.id);
                patchCondition(group.id, c.id, (x) => ({
                  ...x,
                  field: key,
                  op,
                  value: emptyValueFor(op, next.type),
                }));
              }}
            />
          </div>
          <select
            data-testid={`${rowId}-op`}
            aria-label={`${label}: ${t("filters.operator")}`}
            disabled={disabled}
            value={c.op}
            onChange={(e) => {
              const op = e.target.value;
              touch(c.id);
              patchCondition(group.id, c.id, (x) => ({
                ...x,
                op,
                value: valueShape(op) === valueShape(x.op) ? x.value : emptyValueFor(op, def?.type ?? "string"),
              }));
            }}
            className={cn(selectCls, "w-40")}
          >
            {ops.map((op) => (
              <option key={op} value={op}>
                {t(`filters.ops.${op}`, { defaultValue: op })}
              </option>
            ))}
          </select>
          <div className="min-w-0 flex-1 basis-48">
            <FilterValueInput
              field={def}
              op={c.op}
              value={c.value}
              testId={`${rowId}-value`}
              label={label}
              invalid={error !== null}
              errorId={errorId}
              disabled={disabled}
              onChange={(v) => {
                touch(c.id);
                patchCondition(group.id, c.id, (x) => ({ ...x, value: v }));
              }}
            />
          </div>
          <Button
            variant="ghost"
            size="sm"
            disabled={disabled}
            data-testid={`${rowId}-remove`}
            aria-label={`${t("filters.remove")}: ${label}`}
            onClick={() => {
              const rows = group.children.filter((x) => x.kind === "condition").length;
              // next row takes this index; else the previous one; else "add condition"
              focusAfter.current =
                i < rows - 1 ? `${rowId}-field` : i > 0 ? `${prefix}-row-${i - 1}-field` : `${prefix}-add-condition`;
              patchGroup(group.id, (g) => ({ ...g, children: g.children.filter((x) => x.id !== c.id) }));
            }}
          >
            ✕
          </Button>
        </div>
        {error && (
          <p id={errorId} role="alert" className="text-xs text-danger" data-testid={`${rowId}-error`}>
            {t(error)}
          </p>
        )}
      </li>
    );
  };

  const renderGroup = (group: EditGroup, prefix: string, nested: boolean) => {
    const modeBtn = (mode: "all" | "any") => (
      <button
        type="button"
        disabled={disabled}
        aria-pressed={group.mode === mode}
        data-testid={`${prefix}-mode-${mode}`}
        onClick={() => group.mode !== mode && patchGroup(group.id, (x) => ({ ...x, mode }))}
        className={cn(
          "rounded px-2 py-1 text-xs font-medium transition-colors duration-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent disabled:opacity-50",
          group.mode === mode ? "bg-accent-dim text-accent" : "text-muted hover:text-ink",
        )}
      >
        {t(`filters.${mode}`)}
      </button>
    );
    let condIndex = 0;
    let groupIndex = 0;
    return (
      <div
        key={group.id}
        data-testid={nested ? `${prefix}` : undefined}
        className={cn("space-y-2", nested && "rounded-lg border border-hairline bg-surface-subtle p-2.5")}
      >
        <div className="flex flex-wrap items-center gap-2">
          <div role="group" aria-label={t("filters.group")} className="flex gap-1 rounded-md border border-hairline p-0.5">
            {modeBtn("all")}
            {modeBtn("any")}
          </div>
          <label className="flex items-center gap-1.5 text-xs text-muted">
            <input
              type="checkbox"
              disabled={disabled}
              checked={group.negate}
              data-testid={`${prefix}-not`}
              onChange={(e) => patchGroup(group.id, (x) => ({ ...x, negate: e.target.checked }))}
            />
            {t("filters.not")}
          </label>
          {nested && (
            <Button
              variant="ghost"
              size="sm"
              disabled={disabled}
              data-testid={`${prefix}-remove`}
              aria-label={t("filters.removeGroup")}
              onClick={() => {
                const at = model.root.children.filter((x) => x.kind === "group").findIndex((x) => x.id === group.id);
                const groups = model.root.children.filter((x) => x.kind === "group").length;
                focusAfter.current =
                  at < groups - 1
                    ? `${testId}-group-${at}-mode-all`
                    : at > 0
                      ? `${testId}-group-${at - 1}-mode-all`
                      : `${testId}-add-condition`;
                patchGroup(model.root.id, (r) => ({ ...r, children: r.children.filter((x) => x.id !== group.id) }));
              }}
              className="ml-auto"
            >
              ✕
            </Button>
          )}
        </div>
        {group.children.length === 0 ? (
          <p className="text-[13px] text-muted">{t("filters.empty")}</p>
        ) : (
          <ul className="space-y-2">
            {group.children.map((child) => {
              if (child.kind === "group") {
                const at = groupIndex++;
                return (
                  <li key={child.id}>{renderGroup(child, `${testId}-group-${at}`, true)}</li>
                );
              }
              return renderCondition(group, child, condIndex++, prefix);
            })}
          </ul>
        )}
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            disabled={disabled || fields.length === 0}
            data-testid={`${prefix}-add-condition`}
            onClick={() => patchGroup(group.id, (x) => ({ ...x, children: [...x.children, newCondition()] }))}
          >
            + {t("filters.addCondition")}
          </Button>
          {!nested && (
            <Button
              size="sm"
              disabled={disabled || fields.length === 0}
              data-testid={`${prefix}-add-group`}
              onClick={() =>
                patchGroup(group.id, (x) => ({
                  ...x,
                  children: [...x.children, { ...emptyGroup("any"), children: [newCondition()] }] ,
                }))
              }
            >
              + {t("filters.addGroup")}
            </Button>
          )}
        </div>
      </div>
    );
  };

  return (
    <div ref={rootRef} data-testid={testId}>{renderGroup(model.root, testId, false)}</div>
  );
}
