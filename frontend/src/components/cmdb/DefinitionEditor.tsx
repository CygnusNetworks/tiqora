import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { SelectField } from "@/components/ui/SelectField";
import { Switch } from "@/components/ui/Switch";
import {
  ATTR_TYPES,
  addAttr,
  findNode,
  hasNoDefault,
  isNotSearchable,
  moveAttr,
  removeAttr,
  updateAttr,
  type AttrPatch,
  type AttrType,
  type DraftAttr,
} from "./definitionModel";

const INPUT = "w-full rounded border border-hairline bg-surface px-2 py-1.5 text-sm text-ink focus:outline-none focus:ring-1 focus:ring-accent disabled:opacity-60";
const LABEL = "mb-1 block text-xs font-medium uppercase tracking-wide text-muted";
const ID_TYPES: ReadonlySet<AttrType> = new Set(["catalog", "ci_ref", "agent", "service", "sla", "queue", "priority", "ticket_type", "state", "attachment"]);
const LENGTH_TYPES: ReadonlySet<AttrType> = new Set(["text", "textarea", "url", "email"]);
const RANGE_TYPES: ReadonlySet<AttrType> = new Set(["integer", "decimal", "money"]);

export type DefinitionEditorProps = {
  tree: DraftAttr[];
  onChange: (tree: DraftAttr[]) => void;
  selectedUid: string | null;
  onSelect: (uid: string | null) => void;
  /** Problems per attribute (local check and server answer, already worded). */
  issues: Record<string, string>;
  catalogClasses: string[];
  /** Classes a `ci_ref` attribute may be restricted to. */
  classOptions: Array<{ key: string; name: string }>;
  disabled?: boolean;
};

function Row({
  node,
  depth,
  props,
  siblings,
  index,
}: {
  node: DraftAttr;
  depth: number;
  props: DefinitionEditorProps;
  siblings: number;
  index: number;
}) {
  const { t } = useTranslation();
  const { tree, onChange, selectedUid, onSelect, issues, disabled } = props;
  const selected = selectedUid === node.uid;
  const issue = issues[node.uid];
  const iconBtn = "rounded px-1.5 py-0.5 text-xs text-muted hover:bg-surface-subtle hover:text-ink disabled:opacity-40";
  return (
    <>
      <li data-testid={`def-row-${node.uid}`}>
        <div
          className={`flex items-center gap-1 rounded border px-2 py-1 ${selected ? "border-accent bg-accent-dim" : "border-transparent hover:bg-surface-subtle"}`}
          style={{ marginLeft: depth * 16 }}
        >
          <button
            type="button"
            onClick={() => onSelect(node.uid)}
            aria-current={selected}
            data-testid={`def-select-${node.uid}`}
            className="flex min-w-0 flex-1 items-center gap-2 text-left text-sm"
          >
            <span className="truncate font-medium text-ink">{node.name || node.key || t("admin.cmdb.editor.unnamed")}</span>
            <span className="shrink-0 font-mono text-xs text-muted">{node.key}</span>
            <span className="shrink-0 rounded bg-surface-subtle px-1.5 py-0.5 text-xs text-muted">{t(`admin.cmdb.editor.types.${node.type}`)}</span>
            {issue && (
              <span className="shrink-0 text-xs text-danger" role="img" aria-label={issue} title={issue} data-testid={`def-issue-${node.uid}`}>
                !
              </span>
            )}
          </button>
          {!disabled && (
            <span className="flex shrink-0 items-center">
              {node.type === "group" && (
                <button type="button" className={iconBtn} aria-label={t("admin.cmdb.editor.addSub")} data-testid={`def-add-sub-${node.uid}`} onClick={() => {
                  const r = addAttr(tree, node.uid, "text");
                  onChange(r.tree);
                  onSelect(r.uid);
                }}>
                  +
                </button>
              )}
              <button type="button" className={iconBtn} disabled={index === 0} aria-label={t("admin.cmdb.editor.moveUp")} data-testid={`def-up-${node.uid}`} onClick={() => onChange(moveAttr(tree, node.uid, "up"))}>
                ↑
              </button>
              <button type="button" className={iconBtn} disabled={index === siblings - 1} aria-label={t("admin.cmdb.editor.moveDown")} data-testid={`def-down-${node.uid}`} onClick={() => onChange(moveAttr(tree, node.uid, "down"))}>
                ↓
              </button>
              <button type="button" className={`${iconBtn} hover:text-danger`} aria-label={t("admin.cmdb.editor.remove")} data-testid={`def-remove-${node.uid}`} onClick={() => {
                onChange(removeAttr(tree, node.uid));
                if (selected) onSelect(null);
              }}>
                ✕
              </button>
            </span>
          )}
        </div>
      </li>
      {node.sub.map((child, i) => (
        <Row key={child.uid} node={child} depth={depth + 1} props={props} siblings={node.sub.length} index={i} />
      ))}
    </>
  );
}

function Field({ label, htmlFor, children }: { label: string; htmlFor?: string; children: React.ReactNode }) {
  return (
    <div>
      <label className={LABEL} htmlFor={htmlFor}>{label}</label>
      {children}
    </div>
  );
}

function AttributePanel({ node, props }: { node: DraftAttr; props: DefinitionEditorProps }) {
  const { t } = useTranslation();
  const { tree, onChange, issues, catalogClasses, classOptions, disabled } = props;
  const patch = (p: AttrPatch) => onChange(updateAttr(tree, node.uid, p));
  const setOptions = (o: Partial<NonNullable<DraftAttr["options"]>>) => patch({ options: { multiline: false, ...node.options, ...o } });
  const typeItems = ATTR_TYPES.map((ty) => ({ value: ty, label: t(`admin.cmdb.editor.types.${ty}`) }));
  const opts = node.options ?? { multiline: false };
  const numberInput = (id: string, value: number, onValue: (n: number) => void) => (
    <input id={id} data-testid={id} type="number" className={INPUT} disabled={disabled} value={Number.isFinite(value) ? value : ""} onChange={(e) => onValue(e.target.valueAsNumber)} />
  );
  const issue = issues[node.uid];
  const idDefault = ID_TYPES.has(node.type);

  return (
    <div className="space-y-3" data-testid="def-panel">
      {issue && (
        <p role="alert" className="rounded border border-danger/40 bg-danger/10 px-2 py-1.5 text-sm text-danger" data-testid="def-panel-issue">
          {issue}
        </p>
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label={t("admin.cmdb.editor.attrKey")} htmlFor="def-key">
          <input id="def-key" data-testid="def-key" className={`${INPUT} font-mono`} disabled={disabled} maxLength={64} value={node.key} onChange={(e) => patch({ key: e.target.value })} />
        </Field>
        <Field label={t("admin.cmdb.editor.attrName")} htmlFor="def-name">
          <input id="def-name" data-testid="def-name" className={INPUT} disabled={disabled} maxLength={200} value={node.name} onChange={(e) => patch({ name: e.target.value })} />
        </Field>
        <Field label={t("admin.cmdb.editor.attrType")} htmlFor="def-type">
          <SelectField<AttrType> id="def-type" testId="def-type" items={typeItems} value={node.type} disabled={disabled} onChange={(v) => patch({ type: v })} />
        </Field>
        <Field label={t("admin.cmdb.editor.attrHelp")} htmlFor="def-help">
          <input id="def-help" data-testid="def-help" className={INPUT} disabled={disabled} value={node.help ?? ""} onChange={(e) => patch({ help: e.target.value || null })} />
        </Field>
      </div>

      <div className="grid gap-2 sm:grid-cols-3">
        <label className="flex items-center justify-between gap-2 text-sm text-ink">
          {t("admin.cmdb.editor.required")}
          <Switch checked={node.required} disabled={disabled} onChange={(v) => patch({ required: v })} aria-label={t("admin.cmdb.editor.required")} testId="def-required" />
        </label>
        {!isNotSearchable(node.type) && (
          <label className="flex items-center justify-between gap-2 text-sm text-ink">
            {t("admin.cmdb.editor.searchable")}
            <Switch checked={node.searchable} disabled={disabled} onChange={(v) => patch({ searchable: v })} aria-label={t("admin.cmdb.editor.searchable")} testId="def-searchable" />
          </label>
        )}
        <label className="flex items-center justify-between gap-2 text-sm text-ink">
          {t("admin.cmdb.editor.portal")}
          <Switch checked={node.portal} disabled={disabled} onChange={(v) => patch({ portal: v })} aria-label={t("admin.cmdb.editor.portal")} testId="def-portal" />
        </label>
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        <Field label={t("admin.cmdb.editor.countMin")} htmlFor="def-count-min">{numberInput("def-count-min", node.count_min, (n) => patch({ count_min: n }))}</Field>
        <Field label={t("admin.cmdb.editor.countMax")} htmlFor="def-count-max">{numberInput("def-count-max", node.count_max, (n) => patch({ count_max: n }))}</Field>
        <Field label={t("admin.cmdb.editor.countDefault")} htmlFor="def-count-default">{numberInput("def-count-default", node.count_default, (n) => patch({ count_default: n }))}</Field>
      </div>

      {!hasNoDefault(node.type) && (
        <Field label={t("admin.cmdb.editor.default")} htmlFor="def-default">
          <input
            id="def-default"
            data-testid="def-default"
            className={INPUT}
            type={idDefault ? "number" : "text"}
            disabled={disabled}
            value={node.default ?? ""}
            onChange={(e) => {
              const raw = e.target.value;
              patch({ default: raw === "" ? null : idDefault ? Number(raw) : raw });
            }}
          />
        </Field>
      )}

      {LENGTH_TYPES.has(node.type) && (
        <Field label={t("admin.cmdb.editor.optMaxLength")} htmlFor="def-max-length">
          <input id="def-max-length" data-testid="def-max-length" type="number" className={INPUT} disabled={disabled} value={opts.max_length ?? ""} onChange={(e) => setOptions({ max_length: e.target.value === "" ? null : e.target.valueAsNumber })} />
        </Field>
      )}
      {node.type === "text" && (
        <label className="flex items-center justify-between gap-2 text-sm text-ink">
          {t("admin.cmdb.editor.optMultiline")}
          <Switch checked={opts.multiline ?? false} disabled={disabled} onChange={(v) => setOptions({ multiline: v })} aria-label={t("admin.cmdb.editor.optMultiline")} testId="def-multiline" />
        </label>
      )}
      {RANGE_TYPES.has(node.type) && (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t("admin.cmdb.editor.optMin")} htmlFor="def-min">
            <input id="def-min" data-testid="def-min" className={INPUT} disabled={disabled} inputMode="decimal" value={opts.min ?? ""} onChange={(e) => setOptions({ min: e.target.value === "" ? null : e.target.value })} />
          </Field>
          <Field label={t("admin.cmdb.editor.optMax")} htmlFor="def-max">
            <input id="def-max" data-testid="def-max" className={INPUT} disabled={disabled} inputMode="decimal" value={opts.max ?? ""} onChange={(e) => setOptions({ max: e.target.value === "" ? null : e.target.value })} />
          </Field>
        </div>
      )}
      {node.type === "money" && (
        <Field label={t("admin.cmdb.editor.optCurrency")} htmlFor="def-currency">
          <input id="def-currency" data-testid="def-currency" className={`${INPUT} font-mono`} disabled={disabled} maxLength={3} value={opts.currency ?? ""} onChange={(e) => setOptions({ currency: e.target.value.toUpperCase() || null })} />
        </Field>
      )}
      {node.type === "catalog" && (
        <Field label={t("admin.cmdb.editor.optCatalogClass")} htmlFor="def-catalog-class">
          <SelectField<string>
            id="def-catalog-class"
            testId="def-catalog-class"
            items={[...new Set([...catalogClasses, ...(opts.catalog_class ? [opts.catalog_class] : [])])].map((c) => ({ value: c, label: c }))}
            value={opts.catalog_class ?? null}
            disabled={disabled}
            placeholder={t("admin.cmdb.editor.optCatalogPlaceholder")}
            onChange={(v) => setOptions({ catalog_class: v })}
          />
        </Field>
      )}
      {node.type === "ci_ref" && (
        <fieldset data-testid="def-class-keys">
          <legend className={LABEL}>{t("admin.cmdb.editor.optClassKeys")}</legend>
          <p className="mb-1 text-xs text-muted">{t("admin.cmdb.editor.optClassKeysHelp")}</p>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {classOptions.map((c) => {
              const on = (opts.class_keys ?? []).includes(c.key);
              return (
                <label key={c.key} className="flex items-center gap-1.5 text-sm text-ink">
                  <input
                    type="checkbox"
                    data-testid={`def-class-key-${c.key}`}
                    disabled={disabled}
                    checked={on}
                    onChange={(e) => {
                      const cur = opts.class_keys ?? [];
                      setOptions({ class_keys: e.target.checked ? [...cur, c.key] : cur.filter((k) => k !== c.key) });
                    }}
                  />
                  {c.name}
                </label>
              );
            })}
          </div>
        </fieldset>
      )}
      {node.type === "group" && <p className="text-xs text-muted">{t("admin.cmdb.editor.groupHint")}</p>}
    </div>
  );
}

/** Tree of attributes (nested through groups) with the property panel of the selected one. */
export function DefinitionEditor(props: DefinitionEditorProps) {
  const { t } = useTranslation();
  const { tree, onChange, selectedUid, onSelect, disabled } = props;
  const selected = selectedUid ? findNode(tree, selectedUid) : null;
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]" data-testid="definition-editor">
      <div className="space-y-2">
        {tree.length === 0 ? (
          <p className="rounded border border-dashed border-hairline p-3 text-sm text-muted" data-testid="def-empty">{t("admin.cmdb.editor.noAttributes")}</p>
        ) : (
          <ul className="space-y-0.5" data-testid="def-tree">
            {tree.map((n, i) => (
              <Row key={n.uid} node={n} depth={0} props={props} siblings={tree.length} index={i} />
            ))}
          </ul>
        )}
        {!disabled && (
          <Button
            data-testid="def-add"
            onClick={() => {
              const r = addAttr(tree, null, "text");
              onChange(r.tree);
              onSelect(r.uid);
            }}
          >
            {t("admin.cmdb.editor.addAttribute")}
          </Button>
        )}
      </div>
      <div className="rounded-xl border border-hairline bg-surface p-3">
        {selected ? <AttributePanel key={selected.uid} node={selected} props={props} /> : <p className="text-sm text-muted">{t("admin.cmdb.editor.selectHint")}</p>}
      </div>
    </div>
  );
}
