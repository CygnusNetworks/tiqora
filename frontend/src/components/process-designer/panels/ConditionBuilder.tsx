import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { SelectField } from "@/components/ui/SelectField";
import type { CatalogField, DesignerCatalog } from "@/lib/processDesignerApi";
import {
  CONDITION_TYPES,
  LINKINGS,
  duplicateFields,
  isUnknownModule,
  regexpProblem,
  removeGroup,
  type ConditionGroup,
  type ConditionModel,
  type ConditionRow,
  type Linking,
} from "./conditionModel";

const inputCls = "w-full rounded-md border border-hairline bg-surface px-2 py-1 text-sm text-ink";

function ValueInput({
  row,
  field,
  modules,
  testId,
  onChange,
}: {
  row: ConditionRow;
  field: CatalogField | undefined;
  modules: readonly string[];
  testId: string;
  onChange: (match: string) => void;
}) {
  const { t } = useTranslation();
  const label = t("admin.processDesigner.condition.value");
  if (row.matchRaw !== undefined)
    return (
      <input
        readOnly
        className={inputCls}
        aria-label={label}
        data-testid={`${testId}-raw`}
        value={JSON.stringify(row.matchRaw)}
        title={t("admin.processDesigner.condition.rawValue")}
      />
    );
  if (row.type === "Module")
    return (
      <div className="space-y-1">
        <SelectField
          testId={`${testId}-module`}
          aria-label={t("admin.processDesigner.condition.module")}
          placeholder={t("admin.processDesigner.condition.pickModule")}
          value={modules.includes(row.match) ? row.match : null}
          items={modules.map((m) => ({ value: m, label: m }))}
          onChange={onChange}
        />
        <input
          className={inputCls}
          aria-label={t("admin.processDesigner.condition.modulePath")}
          data-testid={`${testId}-value`}
          value={row.match}
          onChange={(e) => onChange(e.target.value)}
        />
      </div>
    );
  const options = field?.options ?? [];
  const kind = field?.kind;
  // Only an exact-match comparison with a list of known values gets a select.
  const exact = row.type === "String" || row.type === "Equal" || row.type === "NotEqual";
  if (exact && options.length > 0) {
    const items = options.map((o) => ({ value: o.value, label: o.label }));
    if (row.match !== "" && !items.some((i) => i.value === row.match)) items.unshift({ value: row.match, label: row.match });
    return <SelectField testId={`${testId}-value`} aria-label={label} value={row.match || null} items={items} onChange={onChange} />;
  }
  if (row.type !== "Regexp" && (kind === "Date" || kind === "DateTime"))
    return (
      <input
        type={kind === "Date" ? "date" : "datetime-local"}
        className={inputCls}
        aria-label={label}
        data-testid={`${testId}-value`}
        value={kind === "Date" ? row.match.slice(0, 10) : row.match.replace(" ", "T").slice(0, 16)}
        onChange={(e) => onChange(kind === "Date" ? e.target.value : e.target.value ? `${e.target.value.replace("T", " ")}:00` : "")}
      />
    );
  return (
    <input className={inputCls} aria-label={label} data-testid={`${testId}-value`} value={row.match} onChange={(e) => onChange(e.target.value)} />
  );
}

function LinkingSelect({
  value,
  onChange,
  testId,
  label,
}: {
  value: Linking;
  onChange: (v: Linking) => void;
  testId: string;
  label: string;
}) {
  const { t } = useTranslation();
  return (
    <SelectField
      testId={testId}
      aria-label={label}
      value={value}
      items={LINKINGS.map((l) => ({ value: l, label: t(`admin.processDesigner.condition.linking.${l}`) }))}
      onChange={onChange}
    />
  );
}

/**
 * Znuny transition condition: overall linking, groups with their own linking and rows of
 * field + type + value. Controlled; the transition panel owns the model and saves it.
 */
export function ConditionBuilder({
  model,
  catalog,
  onChange,
}: {
  model: ConditionModel;
  catalog: DesignerCatalog;
  onChange: (next: ConditionModel) => void;
}) {
  const { t } = useTranslation();
  const condFields = catalog.fields.filter((f) => f.condition);
  const byName = new Map(catalog.fields.map((f) => [f.name, f]));
  const typeItems = CONDITION_TYPES.map((c) => ({ value: c as string, label: t(`admin.processDesigner.condition.type.${c}`) }));

  const patchGroup = (gi: number, p: Partial<ConditionGroup>) =>
    onChange({ ...model, groups: model.groups.map((g, i) => (i === gi ? { ...g, ...p } : g)) });
  const patchRow = (gi: number, ri: number, p: Partial<ConditionRow>) =>
    patchGroup(gi, { rows: model.groups[gi].rows.map((r, i) => (i === ri ? { ...r, ...p } : r)) });

  return (
    <div className="space-y-3" data-testid="condition-builder">
      {model.groups.length === 0 ? (
        <p className="text-sm text-muted" data-testid="condition-empty">
          {t("admin.processDesigner.condition.none")}
        </p>
      ) : (
        <div className="flex items-center gap-2 text-sm text-ink">
          <span>{t("admin.processDesigner.condition.groupsMatch")}</span>
          <LinkingSelect
            testId="condition-linking"
            label={t("admin.processDesigner.condition.groupsMatch")}
            value={model.linking}
            onChange={(linking) => onChange({ ...model, linking })}
          />
        </div>
      )}
      {model.groups.map((g, gi) => {
        const dup = duplicateFields(g);
        return (
          <fieldset key={gi} className="space-y-2 rounded-lg border border-hairline p-2" data-testid={`condition-group-${gi}`}>
            <legend className="px-1 text-xs font-semibold text-muted">
              {t("admin.processDesigner.condition.group")} {gi + 1}
            </legend>
            <div className="flex items-center gap-2 text-sm text-ink">
              <span>{t("admin.processDesigner.condition.rowsMatch")}</span>
              <LinkingSelect
                testId={`condition-group-${gi}-linking`}
                label={t("admin.processDesigner.condition.rowsMatch")}
                value={g.linking}
                onChange={(linking) => patchGroup(gi, { linking })}
              />
              <Button
                size="sm"
                className="ml-auto"
                onClick={() => onChange(removeGroup(model, gi))}
                data-testid={`condition-group-${gi}-remove`}
              >
                {t("admin.processDesigner.condition.removeGroup")}
              </Button>
            </div>
            {g.rows.map((r, ri) => {
              const rid = `condition-row-${gi}-${ri}`;
              const known = condFields.some((f) => f.name === r.field);
              const fieldItems = condFields.map((f) => ({ value: f.name, label: f.label }));
              if (r.field && !known) fieldItems.unshift({ value: r.field, label: r.field });
              const rx = r.type === "Regexp" && r.matchRaw === undefined ? regexpProblem(r.match) : null;
              return (
                <div key={ri} className="space-y-1 rounded-md bg-surface-2 p-2" data-testid={rid}>
                  <SelectField
                    testId={`${rid}-field`}
                    aria-label={t("admin.processDesigner.condition.field")}
                    placeholder={t("admin.processDesigner.condition.field")}
                    value={r.field || null}
                    items={fieldItems}
                    onChange={(field) => patchRow(gi, ri, { field })}
                  />
                  <SelectField
                    testId={`${rid}-type`}
                    aria-label={t("admin.processDesigner.condition.typeLabel")}
                    value={r.type}
                    items={
                      CONDITION_TYPES.some((c) => c === r.type) ? typeItems : [{ value: r.type, label: r.type }, ...typeItems]
                    }
                    onChange={(type) => patchRow(gi, ri, { type })}
                  />
                  <ValueInput
                    row={r}
                    field={byName.get(r.field)}
                    modules={catalog.condition_modules}
                    testId={rid}
                    onChange={(match) => {
                      const { matchRaw: _drop, ...rest } = r;
                      void _drop;
                      patchGroup(gi, { rows: g.rows.map((x, i) => (i === ri ? { ...rest, match } : x)) });
                    }}
                  />
                  {rx ? (
                    <p role="alert" className="text-xs text-danger" data-testid={`${rid}-regexp-error`}>
                      {t(`admin.processDesigner.condition.regexp.${rx}`)}
                    </p>
                  ) : null}
                  {isUnknownModule(r, catalog.condition_modules) ? (
                    <p className="text-xs text-warning" data-testid={`${rid}-module-warning`}>
                      {t("admin.processDesigner.condition.moduleUnknown")}
                    </p>
                  ) : null}
                  <Button
                    size="sm"
                    onClick={() => patchGroup(gi, { rows: g.rows.filter((_, i) => i !== ri) })}
                    data-testid={`${rid}-remove`}
                  >
                    {t("admin.processDesigner.condition.removeField")}
                  </Button>
                </div>
              );
            })}
            {dup.length > 0 ? (
              <p role="alert" className="text-xs text-danger" data-testid={`condition-group-${gi}-duplicate`}>
                {t("admin.processDesigner.condition.duplicate", { fields: dup.join(", ") })}
              </p>
            ) : null}
            <Button
              size="sm"
              onClick={() => patchGroup(gi, { rows: [...g.rows, { field: "", type: "String", match: "" }] })}
              data-testid={`condition-group-${gi}-add-field`}
            >
              {t("admin.processDesigner.condition.addField")}
            </Button>
          </fieldset>
        );
      })}
      <Button
        size="sm"
        onClick={() => onChange({ ...model, groups: [...model.groups, { linking: "and", rows: [{ field: "", type: "String", match: "" }] }] })}
        data-testid="condition-add-group"
      >
        {t("admin.processDesigner.condition.addGroup")}
      </Button>
    </div>
  );
}
