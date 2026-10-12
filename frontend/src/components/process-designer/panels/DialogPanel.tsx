import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { SelectField } from "@/components/ui/SelectField";
import {
  processDesignerApi,
  type CatalogField,
  type DesignerCatalog,
  type DesignerEntity,
} from "@/lib/processDesignerApi";
import { moveItem } from "./activityModel";
import {
  CHANNELS,
  INTERFACES,
  buildDialogConfig,
  editorKindOf,
  fieldHints,
  newFieldModel,
  readDialog,
  type Channel,
  type DialogModel,
  type DisplayMode,
  type FieldModel,
  type TimeUnitsMode,
} from "./dialogFields";
import { PanelFrame } from "./PanelFrame";

const inputCls = "w-full rounded-md border border-hairline bg-surface px-2 py-1 text-sm text-ink";
const DISPLAYS: DisplayMode[] = ["0", "1", "2"];

function toDateTimeLocal(v: string): string {
  return v ? v.replace(" ", "T").slice(0, 16) : "";
}
function fromDateTimeLocal(v: string): string {
  return v ? `${v.replace("T", " ")}:00` : "";
}

function DefaultEditor({
  name,
  field,
  value,
  onChange,
}: {
  name: string;
  field: CatalogField | undefined;
  value: string;
  onChange: (v: string) => void;
}) {
  const { t } = useTranslation();
  const kind = editorKindOf(field, name);
  const tid = `dialog-panel-default-${name}`;
  const label = t("admin.processDesigner.dialog.defaultValue");
  if (kind === "none") return null;
  if (kind === "select") {
    const items = [
      { value: "", label: t("admin.processDesigner.dialog.noDefault") },
      ...(field?.options ?? []).map((o) => ({ value: o.value, label: o.label })),
    ];
    return <SelectField testId={tid} aria-label={label} value={value} items={items} onChange={onChange} />;
  }
  if (kind === "checkbox")
    return (
      <label className="flex items-center gap-2 text-sm text-ink">
        <input type="checkbox" data-testid={tid} checked={value === "1"} onChange={(e) => onChange(e.target.checked ? "1" : "0")} />
        {label}
      </label>
    );
  if (kind === "date")
    return <input type="date" data-testid={tid} aria-label={label} className={inputCls} value={value.slice(0, 10)} onChange={(e) => onChange(e.target.value)} />;
  if (kind === "datetime")
    return (
      <input
        type="datetime-local"
        data-testid={tid}
        aria-label={label}
        className={inputCls}
        value={toDateTimeLocal(value)}
        onChange={(e) => onChange(fromDateTimeLocal(e.target.value))}
      />
    );
  if (kind === "textarea")
    return <textarea data-testid={tid} aria-label={label} className={inputCls} rows={2} value={value} onChange={(e) => onChange(e.target.value)} />;
  return <input data-testid={tid} aria-label={label} className={inputCls} value={value} onChange={(e) => onChange(e.target.value)} />;
}

/**
 * Form properties: texts, interfaces, permission, lock and the ordered field list with Display and
 * typed defaults. Saves itself (`PUT /entities/ActivityDialog/{eid}`).
 */
export function DialogPanel({
  dialog,
  catalog,
  onSaved,
  onReload,
}: {
  dialog: DesignerEntity;
  catalog: DesignerCatalog;
  onSaved: (saved: DesignerEntity) => void;
  onReload: () => void;
}) {
  const { t } = useTranslation();
  const stored = useMemo(() => readDialog(dialog.config), [dialog.config]);
  const [name, setName] = useState(dialog.name);
  const [m, setM] = useState<DialogModel>(stored);
  const [saving, setSaving] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [error, setError] = useState(false);

  const byName = useMemo(() => new Map(catalog.fields.map((f) => [f.name, f])), [catalog.fields]);
  const addable = catalog.fields.filter((f) => f.dialog && !m.order.includes(f.name));
  const dirty = name !== dialog.name || JSON.stringify(m) !== JSON.stringify(stored);
  const noInterface = m.interfaces.length === 0;

  const patch = (p: Partial<DialogModel>) => setM((cur) => ({ ...cur, ...p }));
  const patchField = (fname: string, p: Partial<FieldModel>) =>
    setM((cur) => ({ ...cur, fields: { ...cur.fields, [fname]: { ...cur.fields[fname], ...p } } }));
  const fieldLabel = (fname: string) => {
    const f = byName.get(fname);
    return f && f.label !== f.name ? f.label : t(`admin.processDesigner.dialog.fieldName.${fname}`, { defaultValue: f?.label ?? fname });
  };

  const save = async () => {
    setSaving(true);
    setError(false);
    try {
      const saved = await processDesignerApi.updateEntity("ActivityDialog", dialog.entity_id, {
        expected_version: dialog.version,
        name: name.trim() || dialog.name,
        config: buildDialogConfig(dialog.config, m, catalog.account_time),
      });
      setConflict(false);
      onSaved(saved);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setConflict(true);
      else setError(true);
    } finally {
      setSaving(false);
    }
  };

  const text = (key: string, value: string, set: (v: string) => void, multi = false) => (
    <label className="block space-y-1 text-xs text-muted">
      {t(`admin.processDesigner.dialog.${key}`)}
      {multi ? (
        <textarea rows={2} className={inputCls} value={value} onChange={(e) => set(e.target.value)} data-testid={`dialog-panel-${key}`} />
      ) : (
        <input className={inputCls} value={value} onChange={(e) => set(e.target.value)} data-testid={`dialog-panel-${key}`} />
      )}
    </label>
  );

  return (
    <PanelFrame
      title={t("admin.processDesigner.dialog.title")}
      testId="dialog-panel"
      dirty={dirty && !noInterface}
      saving={saving}
      conflict={conflict}
      error={error}
      onSave={() => void save()}
      onDiscard={() => {
        setName(dialog.name);
        setM(stored);
      }}
      onReload={onReload}
    >
      <label className="block space-y-1 text-xs text-muted">
        {t("admin.processDesigner.dialog.name")}
        <input value={name} onChange={(e) => setName(e.target.value)} className={inputCls} data-testid="dialog-panel-name" />
      </label>
      {text("short", m.short, (v) => patch({ short: v }))}
      {text("long", m.long, (v) => patch({ long: v }), true)}
      {text("submitButton", m.submitButton, (v) => patch({ submitButton: v }))}
      {text("submitAdvice", m.submitAdvice, (v) => patch({ submitAdvice: v }), true)}

      <fieldset className="space-y-1">
        <legend className="text-xs font-medium text-muted">{t("admin.processDesigner.dialog.interfaces")}</legend>
        {INTERFACES.map((iface) => (
          <label key={iface} className="flex items-center gap-2 text-sm text-ink">
            <input
              type="checkbox"
              data-testid={`dialog-panel-iface-${iface}`}
              checked={m.interfaces.includes(iface)}
              onChange={(e) =>
                patch({
                  interfaces: INTERFACES.filter((i) => (i === iface ? e.target.checked : m.interfaces.includes(i))),
                })
              }
            />
            {t(`admin.processDesigner.dialog.iface.${iface}`)}
          </label>
        ))}
        {noInterface ? (
          <p role="alert" className="text-xs text-danger" data-testid="dialog-panel-iface-error">
            {t("admin.processDesigner.dialog.interfaceRequired")}
          </p>
        ) : null}
      </fieldset>

      <div className="space-y-1 text-xs text-muted">
        <span>{t("admin.processDesigner.dialog.permission")}</span>
        <SelectField
          testId="dialog-panel-permission"
          aria-label={t("admin.processDesigner.dialog.permission")}
          value={m.permission}
          items={[
            { value: "", label: t("admin.processDesigner.dialog.noPermission") },
            ...catalog.permissions.filter((p) => p !== "").map((p) => ({ value: p, label: p })),
          ]}
          onChange={(v) => patch({ permission: v })}
        />
      </div>
      <label className="flex items-center gap-2 text-sm text-ink">
        <input type="checkbox" data-testid="dialog-panel-lock" checked={m.requiredLock} onChange={(e) => patch({ requiredLock: e.target.checked })} />
        {t("admin.processDesigner.dialog.requiredLock")}
      </label>

      <div className="space-y-2">
        <h3 className="text-xs font-medium text-muted">{t("admin.processDesigner.dialog.fields")}</h3>
        {m.order.length === 0 ? <p className="text-sm text-muted">{t("admin.processDesigner.dialog.noFields")}</p> : null}
        <ol className="space-y-2" data-testid="dialog-panel-fields">
          {m.order.map((fname, i) => {
            const f = m.fields[fname];
            const label = fieldLabel(fname);
            const hints = fieldHints(fname, f, m.interfaces);
            return (
              <li key={fname} data-testid={`dialog-panel-field-${fname}`} className="space-y-2 rounded-md border border-hairline p-2">
                <div className="flex items-center gap-1">
                  <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink">{label}</span>
                  <Button size="sm" variant="ghost" disabled={i === 0} aria-label={t("admin.processDesigner.dialog.moveUp", { name: label })} onClick={() => patch({ order: moveItem(m.order, i, -1) })} data-testid={`dialog-panel-up-${fname}`}>
                    ↑
                  </Button>
                  <Button size="sm" variant="ghost" disabled={i === m.order.length - 1} aria-label={t("admin.processDesigner.dialog.moveDown", { name: label })} onClick={() => patch({ order: moveItem(m.order, i, 1) })} data-testid={`dialog-panel-down-${fname}`}>
                    ↓
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label={t("admin.processDesigner.dialog.remove", { name: label })}
                    onClick={() => {
                      const { [fname]: _drop, ...rest } = m.fields;
                      patch({ order: m.order.filter((n) => n !== fname), fields: rest });
                    }}
                    data-testid={`dialog-panel-remove-${fname}`}
                  >
                    ×
                  </Button>
                </div>
                <SelectField
                  testId={`dialog-panel-display-${fname}`}
                  aria-label={t("admin.processDesigner.dialog.display")}
                  value={f.display}
                  items={DISPLAYS.map((d) => ({ value: d, label: t(`admin.processDesigner.dialog.displayMode.${d}`) }))}
                  onChange={(v) => patchField(fname, { display: v })}
                />
                <DefaultEditor name={fname} field={byName.get(fname)} value={f.defaultValue} onChange={(v) => patchField(fname, { defaultValue: v })} />
                {fname === "Article" && f.article ? (
                  <div className="space-y-2" data-testid="dialog-panel-article">
                    <SelectField
                      testId="dialog-panel-article-channel"
                      aria-label={t("admin.processDesigner.dialog.channel")}
                      value={f.article.channel}
                      items={CHANNELS.map((c) => ({ value: c, label: t(`admin.processDesigner.dialog.channelName.${c}`) }))}
                      onChange={(v: Channel) => patchField(fname, { article: { ...f.article!, channel: v } })}
                    />
                    <label className="flex items-center gap-2 text-sm text-ink">
                      <input
                        type="checkbox"
                        data-testid="dialog-panel-article-visible"
                        checked={f.article.visibleForCustomer}
                        onChange={(e) => patchField(fname, { article: { ...f.article!, visibleForCustomer: e.target.checked } })}
                      />
                      {t("admin.processDesigner.dialog.visibleForCustomer")}
                    </label>
                    {catalog.account_time ? (
                      <SelectField
                        testId="dialog-panel-article-time"
                        aria-label={t("admin.processDesigner.dialog.timeUnits")}
                        value={f.article.timeUnits}
                        items={(["0", "1", "2"] as TimeUnitsMode[]).map((u) => ({ value: u, label: t(`admin.processDesigner.dialog.timeUnitsMode.${u}`) }))}
                        onChange={(v: TimeUnitsMode) => patchField(fname, { article: { ...f.article!, timeUnits: v } })}
                      />
                    ) : null}
                  </div>
                ) : null}
                <input className={inputCls} aria-label={t("admin.processDesigner.dialog.short")} value={f.short} onChange={(e) => patchField(fname, { short: e.target.value })} data-testid={`dialog-panel-fshort-${fname}`} />
                <input className={inputCls} aria-label={t("admin.processDesigner.dialog.long")} value={f.long} onChange={(e) => patchField(fname, { long: e.target.value })} data-testid={`dialog-panel-flong-${fname}`} />
                {hints.map((h) => (
                  <p key={h} className="text-xs text-warning" data-testid={`dialog-panel-hint-${fname}-${h}`}>
                    {t(`admin.processDesigner.dialog.hint.${h}`)}
                  </p>
                ))}
              </li>
            );
          })}
        </ol>
        {addable.length > 0 ? (
          <SelectField
            testId="dialog-panel-add"
            aria-label={t("admin.processDesigner.dialog.addField")}
            placeholder={t("admin.processDesigner.dialog.addField")}
            value={null}
            items={addable.map((f) => ({ value: f.name, label: fieldLabel(f.name) }))}
            onChange={(n) => setM((cur) => ({ ...cur, order: [...cur.order, n], fields: { ...cur.fields, [n]: newFieldModel(n) } }))}
          />
        ) : null}
      </div>
    </PanelFrame>
  );
}
