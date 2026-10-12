import { useId, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { processDesignerApi, type DesignerCatalog, type DesignerEntity } from "@/lib/processDesignerApi";
import {
  ACTION_GROUPS,
  MODULE_PREFIX,
  actionFieldDefs,
  buildActionConfig,
  groupOfAction,
  moduleOf,
  readActionConfig,
  shortModule,
  type ActionFormState,
  type CatalogAction,
} from "./actionForm";
import { PanelFrame } from "./PanelFrame";

const inputCls = "w-full rounded-md border border-hairline bg-surface px-2 py-1 text-sm text-ink";

/** Free text with suggestions: a typed select that still accepts smart tags like `<OTRS_TICKET_Queue>`. */
function Combo({
  value,
  onChange,
  options,
  multiline,
  testId,
  ariaLabel,
}: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
  multiline?: boolean;
  testId: string;
  ariaLabel: string;
}) {
  const listId = useId();
  if (multiline)
    return (
      <textarea
        className={inputCls}
        rows={3}
        value={value}
        aria-label={ariaLabel}
        data-testid={testId}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  return (
    <>
      <input
        className={inputCls}
        value={value}
        list={options.length > 0 ? listId : undefined}
        aria-label={ariaLabel}
        data-testid={testId}
        autoComplete="off"
        onChange={(e) => onChange(e.target.value)}
      />
      {options.length > 0 ? (
        <datalist id={listId}>
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label !== o.value ? o.label : undefined}
            </option>
          ))}
        </datalist>
      ) : null}
    </>
  );
}

/**
 * Action properties: name, module and the module's parameters. Saves itself
 * (`PUT /entities/TransitionAction/{eid}`); unknown keys stay editable as raw rows.
 */
export function ActionPanel({
  action,
  catalog,
  onSaved,
  onReload,
}: {
  action: DesignerEntity;
  catalog: DesignerCatalog;
  onSaved: (saved: DesignerEntity) => void;
  onReload: () => void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState(action.name);
  const [module, setModule] = useState(moduleOf(action.config));
  const spec = useMemo<CatalogAction | undefined>(
    () => catalog.actions.find((a) => a.name === shortModule(module)),
    [catalog.actions, module],
  );
  const [state, setState] = useState<ActionFormState>(() => readActionConfig(spec, action.config));
  const [saving, setSaving] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [error, setError] = useState(false);

  const defs = useMemo(() => actionFieldDefs(spec, catalog), [spec, catalog]);
  const groups = useMemo(() => {
    const keysOf = (id: string) => id.split("|");
    return new Map(defs.map((d) => [d.key, keysOf(d.key)]));
  }, [defs]);

  const pickModule = (shortName: string) => {
    const next = catalog.actions.find((a) => a.name === shortName);
    setModule(MODULE_PREFIX + shortName);
    // typed values of the old module survive as raw rows, nothing is silently dropped
    const merged = buildActionConfig(spec, action.config, state);
    setState(readActionConfig(next, { ...action.config, Config: merged.Config }));
  };

  const nextConfig = buildActionConfig(spec, { ...action.config, Module: module }, state);
  const baseline = useMemo(() => {
    const storedSpec = catalog.actions.find((a) => a.name === shortModule(moduleOf(action.config)));
    return buildActionConfig(storedSpec, action.config, readActionConfig(storedSpec, action.config));
  }, [catalog.actions, action.config]);
  const dirty = name !== action.name || JSON.stringify(nextConfig) !== JSON.stringify(baseline);

  const save = async () => {
    setSaving(true);
    setError(false);
    try {
      const saved = await processDesignerApi.updateEntity("TransitionAction", action.entity_id, {
        expected_version: action.version,
        name: name.trim() || action.name,
        config: nextConfig,
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
  const discard = () => {
    setName(action.name);
    setModule(moduleOf(action.config));
    setState(readActionConfig(catalog.actions.find((a) => a.name === shortModule(moduleOf(action.config))), action.config));
  };

  const setField = (id: string, patch: Partial<{ key: string; value: string }>) =>
    setState((cur) => ({ ...cur, fields: { ...cur.fields, [id]: { ...cur.fields[id], ...patch } } }));
  const setRaw = (i: number, patch: Partial<{ key: string; value: string }>) =>
    setState((cur) => ({ ...cur, raw: cur.raw.map((r, j) => (j === i ? { ...r, ...patch } : r)) }));

  const byGroup = ACTION_GROUPS.map((g) => ({
    group: g,
    items: catalog.actions.filter((a) => groupOfAction(a.name) === g),
  })).filter((g) => g.items.length > 0);
  const currentShort = shortModule(module);

  return (
    <PanelFrame
      title={t("admin.processDesigner.panelAction.title")}
      testId="action-panel"
      dirty={dirty}
      saving={saving}
      conflict={conflict}
      error={error}
      onSave={() => void save()}
      onDiscard={discard}
      onReload={onReload}
    >
      <label className="block space-y-1 text-xs text-muted">
        {t("admin.processDesigner.condition.name")}
        <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} data-testid="action-panel-name" />
      </label>
      <label className="block space-y-1 text-xs text-muted">
        {t("admin.processDesigner.action.module")}
        <select
          className={inputCls}
          value={currentShort}
          onChange={(e) => pickModule(e.target.value)}
          data-testid="action-panel-module"
        >
          {!spec ? <option value={currentShort}>{currentShort || "–"}</option> : null}
          {byGroup.map(({ group, items }) => (
            <optgroup key={group} label={t(`admin.processDesigner.action.group.${group}`)}>
              {items.map((a) => (
                <option key={a.name} value={a.name}>
                  {a.name}
                  {!a.supported ? ` (${t("admin.processDesigner.action.unsupported")})` : ""}
                  {a.tiqora_only ? ` (${t("admin.processDesigner.action.tiqoraOnly")})` : ""}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      </label>
      {spec && !spec.supported ? (
        <span className="inline-block rounded border border-hairline px-1.5 py-0.5 text-xs text-muted" data-testid="action-panel-badge">
          {t("admin.processDesigner.action.unsupported")}
        </span>
      ) : null}
      {spec?.tiqora_only ? (
        <span className="inline-block rounded border border-hairline px-1.5 py-0.5 text-xs text-muted" data-testid="action-panel-badge">
          {t("admin.processDesigner.action.tiqoraOnly")}
        </span>
      ) : null}
      {!spec ? (
        <p className="break-all text-xs text-muted" data-testid="action-panel-unknown">
          {t("admin.processDesigner.action.unknownModule", { module: module || "–" })}
        </p>
      ) : null}

      {defs.map((d) => {
        const keys = groups.get(d.key) ?? [d.key];
        const f = state.fields[d.key] ?? { key: keys[0], value: "" };
        return (
          <div key={d.key} className="space-y-1" data-testid={`action-field-${d.key}`}>
            <div className="flex items-center justify-between gap-2 text-xs text-muted">
              <span>
                {keys.length === 1 ? keys[0] : f.key}
                {d.required ? " *" : ""}
              </span>
              {keys.length > 1 ? (
                <select
                  className="rounded border border-hairline bg-surface px-1 py-0.5 text-xs text-ink"
                  value={f.key}
                  aria-label={t("admin.processDesigner.action.oneOf")}
                  data-testid={`action-field-${d.key}-mode`}
                  onChange={(e) => setField(d.key, { key: e.target.value })}
                >
                  {keys.map((k, i) => (
                    <option key={k} value={k}>
                      {i === 0 ? t("admin.processDesigner.action.byName") : t("admin.processDesigner.action.byId")}
                    </option>
                  ))}
                </select>
              ) : null}
            </div>
            <Combo
              value={f.value}
              onChange={(v) => setField(d.key, { value: v })}
              options={d.options ?? []}
              multiline={d.kind === "textarea"}
              testId={`action-field-${d.key}-input`}
              ariaLabel={f.key}
            />
          </div>
        );
      })}

      <div className="space-y-2" data-testid="action-panel-raw">
        <h3 className="text-xs font-medium text-muted">{t("admin.processDesigner.action.rawParams")}</h3>
        {state.raw.map((r, i) => (
          <div key={i} className="flex items-center gap-1" data-testid={`action-raw-${i}`}>
            <input
              className={inputCls}
              value={r.key}
              // A non-scalar value cannot be edited here; renaming its key would drop it.
              readOnly={r.locked}
              aria-label={t("admin.processDesigner.action.rawKey")}
              data-testid={`action-raw-${i}-key`}
              onChange={(e) => setRaw(i, { key: e.target.value })}
            />
            <input
              className={inputCls}
              value={r.value}
              readOnly={r.locked}
              aria-label={t("admin.processDesigner.action.rawValue")}
              data-testid={`action-raw-${i}-value`}
              onChange={(e) => setRaw(i, { value: e.target.value })}
            />
            <Button
              size="sm"
              variant="ghost"
              aria-label={t("admin.processDesigner.action.rawRemove", { key: r.key })}
              data-testid={`action-raw-${i}-remove`}
              onClick={() => setState((cur) => ({ ...cur, raw: cur.raw.filter((_, j) => j !== i) }))}
            >
              ×
            </Button>
          </div>
        ))}
        <Button
          size="sm"
          data-testid="action-raw-add"
          onClick={() => setState((cur) => ({ ...cur, raw: [...cur.raw, { key: "", value: "", locked: false }] }))}
        >
          {t("admin.processDesigner.action.rawAdd")}
        </Button>
      </div>

      <div className="space-y-1">
        <h3 className="text-xs font-medium text-muted">{t("admin.processDesigner.action.moreSettings")}</h3>
        <label className="block space-y-1 text-xs text-muted">
          {t("admin.processDesigner.action.userOverride")}
          <input
            className={inputCls}
            value={state.userId}
            placeholder={t("admin.processDesigner.action.userOverrideHint")}
            data-testid="action-panel-userid"
            onChange={(e) => setState((cur) => ({ ...cur, userId: e.target.value }))}
          />
        </label>
      </div>
    </PanelFrame>
  );
}
