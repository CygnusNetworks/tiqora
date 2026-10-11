import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { FieldsForm, registerCoreFieldKinds, type FieldDef, type FieldValues } from "@/components/fields";
import { Button } from "@/components/ui/Button";
import { SelectField } from "@/components/ui/SelectField";
import { Switch } from "@/components/ui/Switch";
import { setValues, type PrechatField } from "@/lib/webchatForm";
import { Field, inputClass, type TabProps } from "./shared";
import type { RefOption } from "./GeneralTab";

registerCoreFieldKinds();

type Kind = PrechatField["kind"];
type MapsTo = NonNullable<PrechatField["maps_to"]>;
const KINDS: Kind[] = ["text", "textarea", "email", "select", "checkbox"];
const MAPS: MapsTo[] = ["none", "name", "email", "dynamic_field"];
const MAX_FIELDS = 15;

const blankField = (): PrechatField => ({
  key: "",
  kind: "text",
  label: "",
  required: false,
  options: [],
  maps_to: "none",
  dynamic_field: null,
});

/** The wave-0 field engine renders the preview: email behaves like text, checkbox like boolean. */
function toFieldDef(f: PrechatField, key: string): FieldDef {
  const base = { key, label: f.label.trim() || f.key || key, required: f.required };
  switch (f.kind) {
    case "textarea":
      return { ...base, kind: "textarea" };
    case "select":
      return {
        ...base,
        kind: "select",
        allowEmpty: true,
        options: (f.options ?? []).filter((o) => o.value).map((o) => ({ value: o.value, label: o.label || o.value })),
      };
    case "checkbox":
      return { ...base, kind: "boolean" };
    default:
      return { ...base, kind: "text" };
  }
}

export function PrechatTab({
  draft,
  errors,
  onDraft,
  dynamicFields,
}: TabProps & { dynamicFields: RefOption<string>[] }) {
  const { t } = useTranslation();
  const fields = draft.values.prechat_fields;
  const [previewValues, setPreviewValues] = useState<FieldValues>({});

  const setFields = (next: PrechatField[]) => onDraft(setValues(draft, { prechat_fields: next }));
  const patch = (i: number, p: Partial<PrechatField>) => setFields(fields.map((f, j) => (j === i ? { ...f, ...p } : f)));
  const move = (i: number, by: -1 | 1) => {
    const to = i + by;
    if (to < 0 || to >= fields.length) return;
    const next = [...fields];
    [next[i], next[to]] = [next[to], next[i]];
    setFields(next);
  };

  const previewDefs = useMemo(() => {
    const used = new Set<string>();
    return fields.map((f, i) => {
      let key = f.key || `field_${i + 1}`;
      while (used.has(key)) key = `${key}_`;
      used.add(key);
      return toFieldDef(f, key);
    });
  }, [fields]);

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]" data-testid="webchat-tab-prechat">
      <div className="space-y-3">
        <p className="text-xs text-muted">{t("webchat.admin.prechat.hint")}</p>
        {errors.prechat_fields && (
          <p role="alert" className="text-xs text-danger">
            {errors.prechat_fields}
          </p>
        )}
        {fields.map((f, i) => {
          const p = `prechat_fields.${i}`;
          const opts = f.options ?? [];
          const setOptions = (options: { value: string; label: string }[]) => patch(i, { options });
          return (
            <div key={i} data-testid={`webchat-field-${i}`} className="space-y-2 rounded-md border border-hairline bg-surface-subtle/40 p-2.5">
              <div className="grid gap-2 sm:grid-cols-2">
                <Field id={`field-${i}-key`} label={t("webchat.admin.prechat.key")} error={errors[`${p}.key`]}>
                  <input
                    id={`webchat-field-${i}-key`}
                    data-testid={`webchat-field-${i}-key`}
                    className={`${inputClass} font-mono`}
                    value={f.key}
                    aria-invalid={!!errors[`${p}.key`]}
                    onChange={(e) => patch(i, { key: e.target.value })}
                  />
                </Field>
                <Field id={`field-${i}-label`} label={t("webchat.admin.prechat.label")} error={errors[`${p}.label`]}>
                  <input
                    id={`webchat-field-${i}-label`}
                    data-testid={`webchat-field-${i}-label`}
                    className={inputClass}
                    value={f.label}
                    aria-invalid={!!errors[`${p}.label`]}
                    onChange={(e) => patch(i, { label: e.target.value })}
                  />
                </Field>
                <Field id={`field-${i}-kind`} label={t("webchat.admin.prechat.kind")}>
                  <SelectField
                    id={`webchat-field-${i}-kind`}
                    testId={`webchat-field-${i}-kind`}
                    items={KINDS.map((k) => ({ value: k, label: t(`webchat.admin.prechat.kinds.${k}`) }))}
                    value={f.kind}
                    onChange={(kind) =>
                      patch(i, { kind, options: kind === "select" ? opts : [] })
                    }
                  />
                </Field>
                <Field id={`field-${i}-maps`} label={t("webchat.admin.prechat.mapsTo")} error={errors[`${p}.maps_to`]}>
                  <SelectField
                    id={`webchat-field-${i}-maps`}
                    testId={`webchat-field-${i}-maps`}
                    items={MAPS.map((m) => ({ value: m, label: t(`webchat.admin.prechat.maps.${m}`) }))}
                    value={f.maps_to ?? "none"}
                    invalid={!!errors[`${p}.maps_to`]}
                    onChange={(maps_to) =>
                      patch(i, {
                        maps_to,
                        kind: maps_to === "email" ? "email" : maps_to === "name" ? "text" : f.kind,
                        dynamic_field: maps_to === "dynamic_field" ? f.dynamic_field : null,
                      })
                    }
                  />
                </Field>
                {f.maps_to === "dynamic_field" && (
                  <Field id={`field-${i}-df`} label={t("webchat.admin.prechat.dynamicField")} error={errors[`${p}.dynamic_field`]} className="sm:col-span-2">
                    <SelectField
                      id={`webchat-field-${i}-df`}
                      testId={`webchat-field-${i}-df`}
                      items={dynamicFields}
                      value={f.dynamic_field}
                      placeholder={t("webchat.admin.prechat.pickDynamicField")}
                      invalid={!!errors[`${p}.dynamic_field`]}
                      onChange={(dynamic_field) => patch(i, { dynamic_field })}
                    />
                  </Field>
                )}
              </div>

              {f.kind === "select" && (
                <div className="space-y-1.5" data-testid={`webchat-field-${i}-options`}>
                  <span className="block text-xs font-medium text-muted">{t("webchat.admin.prechat.options")}</span>
                  {opts.map((o, j) => (
                    <div key={j} className="flex items-center gap-2">
                      <input
                        aria-label={t("webchat.admin.prechat.optionValue")}
                        placeholder={t("webchat.admin.prechat.optionValue")}
                        data-testid={`webchat-field-${i}-option-${j}-value`}
                        className={`${inputClass} font-mono`}
                        value={o.value}
                        onChange={(e) => setOptions(opts.map((x, k) => (k === j ? { ...x, value: e.target.value } : x)))}
                      />
                      <input
                        aria-label={t("webchat.admin.prechat.optionLabel")}
                        placeholder={t("webchat.admin.prechat.optionLabel")}
                        data-testid={`webchat-field-${i}-option-${j}-label`}
                        className={inputClass}
                        value={o.label}
                        onChange={(e) => setOptions(opts.map((x, k) => (k === j ? { ...x, label: e.target.value } : x)))}
                      />
                      <Button size="sm" variant="ghost" data-testid={`webchat-field-${i}-option-${j}-remove`} onClick={() => setOptions(opts.filter((_, k) => k !== j))}>
                        {t("webchat.admin.remove")}
                      </Button>
                    </div>
                  ))}
                  <Button size="sm" data-testid={`webchat-field-${i}-option-add`} disabled={opts.length >= 50} onClick={() => setOptions([...opts, { value: "", label: "" }])}>
                    {t("webchat.admin.prechat.addOption")}
                  </Button>
                  {errors[`${p}.options`] && (
                    <p role="alert" data-testid={`webchat-field-${i}-options-error`} className="text-xs text-danger">
                      {errors[`${p}.options`]}
                    </p>
                  )}
                </div>
              )}

              <div className="flex flex-wrap items-center justify-between gap-2">
                <label className="flex items-center gap-2 text-sm text-ink">
                  <Switch
                    checked={f.required}
                    onChange={(required) => patch(i, { required })}
                    testId={`webchat-field-${i}-required`}
                    aria-label={t("webchat.admin.prechat.required")}
                  />
                  {t("webchat.admin.prechat.required")}
                </label>
                <div className="flex gap-1">
                  <Button size="sm" variant="ghost" disabled={i === 0} aria-label={t("webchat.admin.general.moveUp")} data-testid={`webchat-field-${i}-up`} onClick={() => move(i, -1)}>
                    ↑
                  </Button>
                  <Button size="sm" variant="ghost" disabled={i === fields.length - 1} aria-label={t("webchat.admin.general.moveDown")} data-testid={`webchat-field-${i}-down`} onClick={() => move(i, 1)}>
                    ↓
                  </Button>
                  <Button size="sm" variant="ghost" data-testid={`webchat-field-${i}-remove`} onClick={() => setFields(fields.filter((_, j) => j !== i))}>
                    {t("webchat.admin.remove")}
                  </Button>
                </div>
              </div>
            </div>
          );
        })}
        <Button size="sm" data-testid="webchat-field-add" disabled={fields.length >= MAX_FIELDS} onClick={() => setFields([...fields, blankField()])}>
          {t("webchat.admin.prechat.addField")}
        </Button>
      </div>

      <div className="space-y-2" data-testid="webchat-prechat-preview-box">
        <h3 className="text-sm font-semibold text-ink">{t("webchat.admin.prechat.preview")}</h3>
        <div className="rounded-lg border border-hairline bg-surface p-3">
          {fields.length === 0 ? (
            <p className="text-xs text-muted">{t("webchat.admin.prechat.previewEmpty")}</p>
          ) : (
            <FieldsForm
              fields={previewDefs}
              values={previewValues}
              onChange={(key, value) => setPreviewValues((cur) => ({ ...cur, [key]: value }))}
              testId="webchat-prechat-preview"
            />
          )}
        </div>
      </div>
    </div>
  );
}
