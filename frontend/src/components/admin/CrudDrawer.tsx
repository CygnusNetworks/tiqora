import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { HelpPopover } from "@/components/ui/HelpPopover";
import { SelectMenu } from "@/components/ui/SelectMenu";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { Switch } from "@/components/ui/Switch";
import { UnitInput } from "@/components/ui/UnitInput";
import { ChevronDownIcon } from "@/components/ui/icons";
import { cn } from "@/lib/cn";

export type FieldOption = { value: string | number; label: string };

export type FieldType =
  | "text"
  | "textarea"
  | "number"
  | "checkbox"
  | "select"
  | "password"
  | "section"
  | "switch"
  | "segmented"
  | "custom";

export type FieldValues = Record<string, unknown>;

export type CustomFieldContext = {
  /** Write any form field — for one control that edits several fields. */
  setValue: (name: string, v: unknown) => void;
  /** id / data-testid of a field's control: `${testIdPrefix}-${name}`. */
  idFor: (name: string) => string;
  invalid: boolean;
};

export type FieldDef = {
  name: string;
  label: string;
  type: FieldType;
  /** Static, or computed from the form's current values (e.g. required only
   * when a sibling toggle picks a certain mode). */
  required?: boolean | ((values: FieldValues) => boolean);
  /** Choices for "select" and "segmented". */
  options?: FieldOption[];
  placeholder?: string;
  /** Static, or computed from the form's current values. */
  helpText?: string | ((values: FieldValues) => string | undefined);
  /**
   * When true, render the control in monospace (IDs, code snippets).
   * Default false — prose fields (signatures, templates, comments) use the
   * proportional UI font. Opt in only for genuinely monospaced content.
   */
  mono?: boolean;
  /** Textarea row count (default 4). */
  rows?: number;
  /** Hard cap for "text"/"password" inputs, where the backend enforces one
   * too (e.g. the password length policy). */
  maxLength?: number;
  /**
   * Layout width. Short scalar fields (numbers, small selects) read better
   * side by side; adjacent `half` fields share a row. Default "full".
   * With appearance="settings" only an explicit "half" pairs.
   */
  width?: "full" | "half";
  /** Only for type "custom": renders its own control. */
  render?: (
    value: unknown,
    onChange: (v: unknown) => void,
    values: FieldValues,
    ctx: CustomFieldContext,
  ) => ReactNode;
  /** "number" only: unit drawn inside the input ("Min.", "%"). */
  unit?: string;
  /** "switch" only: stored values for on/off (default true/false), e.g.
   * `{ on: 1, off: 2 }` for valid_id. Any other stored value reads as off
   * and is kept until the switch is touched. */
  switchValues?: { on: unknown; off: unknown };
  /** "switch" only: text next to the switch for the current state. */
  switchLabels?: { on: string; off: string };
  /**
   * appearance="settings" only. "inline" (default): label and hint on the
   * left, control on the right. "block": the control spans the row (tables,
   * matrices); with an empty label it runs edge to edge.
   */
  layout?: "inline" | "block";
  /** appearance="settings" only: tinted row (e.g. the last row of a block). */
  rowTone?: "subtle";
  /**
   * Optional secondary UI below the control (e.g. variable picker for body
   * fields). Only resources that set this are affected; other forms unchanged.
   */
  afterControl?: (ctx: {
    value: unknown;
    onChange: (v: unknown) => void;
    values: FieldValues;
    controlId: string;
  }) => ReactNode;
  /** Hide this field for create (e.g. immutable identity fields shown read-only). */
  hideOnCreate?: boolean;
  /** Hide this field for edit (e.g. a create-only "how to set the password" toggle). */
  hideOnEdit?: boolean;
  /** Conditionally show/hide based on other fields' current values (e.g. a
   * password field hidden while "auto-generate" is selected). */
  showIf?: (values: FieldValues) => boolean;
  /**
   * Tab this field belongs to (already-translated label). When two or more
   * distinct tabs occur across the visible fields the drawer renders a tab
   * bar and shows one tab at a time; otherwise it lays every field out in a
   * single column exactly as before.
   */
  tab?: string;
  /**
   * Opt-in ⓘ popover rendered next to the label — for fields whose meaning or
   * default isn't obvious from the label alone. Distinct from `helpText`
   * (always-visible static hint below the control).
   */
  help?: { title: string; description: ReactNode; defaultHint?: string };
};

export type CrudDrawerProps = {
  open: boolean;
  onClose: () => void;
  title: string;
  /** Optional one-liner under the title (passed through to Dialog). */
  description?: string;
  fields: FieldDef[];
  initialValues: FieldValues;
  mode: "create" | "edit";
  onSubmit: (values: FieldValues) => Promise<void>;
  submitError?: string | null;
  testIdPrefix?: string;
  size?: "sm" | "md" | "lg" | "xl" | "2xl";
  /**
   * "default": stacked fields with small uppercase labels (every admin form).
   * "settings": sentence-case labels, label + hint on the left and the
   * control on the right, rows grouped into bordered blocks; a `section`
   * field becomes a heading and/or intro line above the next block (an empty
   * label renders only the intro). Opt-in per resource.
   */
  appearance?: "default" | "settings";
  /** Tabbed forms only: stack every tab in one grid cell and show one at a
   * time, so the dialog is as tall as its tallest tab and does not jump when
   * switching. Fields of hidden tabs stay mounted (inert). */
  stableTabHeight?: boolean;
  /** Short live summary left in the footer (e.g. "2 stages active"). */
  footerStatus?: (values: FieldValues) => ReactNode;
};

function isEmpty(v: unknown): boolean {
  return v === undefined || v === null || v === "";
}

/** Group consecutive checkbox fields so they render as one bordered block
 * instead of a ragged column of bare checkboxes; pair adjacent half-width
 * fields onto a shared row. */
type Row =
  | { kind: "field"; field: FieldDef }
  | { kind: "pair"; fields: [FieldDef, FieldDef] }
  | { kind: "checkboxes"; fields: FieldDef[] };

function layoutRows(fields: FieldDef[]): Row[] {
  const rows: Row[] = [];
  let i = 0;
  while (i < fields.length) {
    const f = fields[i];
    if (f.type === "checkbox") {
      const group: FieldDef[] = [];
      while (i < fields.length && fields[i].type === "checkbox") {
        group.push(fields[i]);
        i++;
      }
      rows.push({ kind: "checkboxes", fields: group });
      continue;
    }
    const isHalf = (d: FieldDef) => d.width === "half" || (d.width == null && d.type === "number");
    const next = fields[i + 1];
    if (isHalf(f) && next && isHalf(next) && next.type !== "checkbox") {
      rows.push({ kind: "pair", fields: [f, next] });
      i += 2;
      continue;
    }
    rows.push({ kind: "field", field: f });
    i++;
  }
  return rows;
}

/** Settings appearance: sections split the list into bordered blocks; inside
 * a block only explicitly half-width neighbours share a row. */
type SettingsBlock =
  | { kind: "section"; field: FieldDef }
  | { kind: "group"; rows: ({ kind: "field"; field: FieldDef } | { kind: "pair"; fields: [FieldDef, FieldDef] })[] };

function layoutSettings(fields: FieldDef[]): SettingsBlock[] {
  const blocks: SettingsBlock[] = [];
  for (let i = 0; i < fields.length; i++) {
    const f = fields[i];
    if (f.type === "section") {
      blocks.push({ kind: "section", field: f });
      continue;
    }
    let group = blocks[blocks.length - 1];
    if (!group || group.kind !== "group") {
      group = { kind: "group", rows: [] };
      blocks.push(group);
    }
    const next = fields[i + 1];
    if (f.width === "half" && next && next.width === "half" && next.type !== "section") {
      group.rows.push({ kind: "pair", fields: [f, next] });
      i++;
    } else {
      group.rows.push({ kind: "field", field: f });
    }
  }
  return blocks;
}

const FOCUSABLE_CONTROL = 'input, textarea, button, select, [tabindex="0"]';

/**
 * Generic create/edit form host built on the shared Dialog. Column defs stay
 * in the resource page; this renders inputs from FieldDef[], does required-
 * field validation (focusing the first invalid control), lays short fields
 * out two-up, groups checkbox runs, and keeps the action bar fixed below the
 * scrolling body. Cmd/Ctrl+Enter submits from anywhere in the form.
 */
export function CrudDrawer({
  open,
  onClose,
  title,
  description,
  fields,
  initialValues,
  mode,
  onSubmit,
  submitError,
  testIdPrefix = "admin-form",
  size = "lg",
  appearance = "default",
  stableTabHeight = false,
  footerStatus,
}: CrudDrawerProps) {
  const { t } = useTranslation();
  const [values, setValues] = useState<FieldValues>(initialValues);
  const [errors, setErrors] = useState<Record<string, boolean>>({});
  const [submitting, setSubmitting] = useState(false);
  const [activeTab, setActiveTab] = useState<string | null>(null);
  const formRef = useRef<HTMLFormElement | null>(null);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const settings = appearance === "settings";

  useEffect(() => {
    if (open) {
      setValues(initialValues);
      setErrors({});
      setActiveTab(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const setField = (name: string, v: unknown) => {
    setValues((prev) => ({ ...prev, [name]: v }));
    setErrors((prev) => (prev[name] ? { ...prev, [name]: false } : prev));
  };

  const isRequired = (f: FieldDef): boolean =>
    typeof f.required === "function" ? f.required(values) : Boolean(f.required);

  const resolveHelpText = (f: FieldDef): string | undefined =>
    typeof f.helpText === "function" ? f.helpText(values) : f.helpText;

  const visibleFields = fields.filter(
    (f) =>
      !(mode === "create" && f.hideOnCreate) &&
      !(mode === "edit" && f.hideOnEdit) &&
      (f.showIf ? f.showIf(values) : true),
  );

  // Tabs are opt-in: a resource that never sets `tab` renders exactly as
  // before. Order follows first appearance in the field list.
  const tabs: string[] = [];
  for (const f of visibleFields) {
    if (f.tab && !tabs.includes(f.tab)) tabs.push(f.tab);
  }
  const tabbed = tabs.length > 1;
  const currentTab = tabbed ? (activeTab !== null && tabs.includes(activeTab) ? activeTab : tabs[0]) : null;
  const fieldsInTab = (tab: string) => visibleFields.filter((f) => f.tab === tab);
  const shownFields = currentTab === null ? visibleFields : fieldsInTab(currentTab);

  /** Required-but-empty count, so a tab you cannot see can still ask for
   * attention instead of only failing at save time. */
  const missingIn = (tab: string) =>
    fieldsInTab(tab).filter((f) => isRequired(f) && isEmpty(values[f.name])).length;

  const idFor = (name: string) => `${testIdPrefix}-${name}`;
  const tabId = (idx: number) => `${testIdPrefix}-tab-${idx}`;
  const panelId = (idx: number) => `${testIdPrefix}-tabpanel-${idx}`;

  const focusField = (name: string) => {
    const el = formRef.current?.querySelector<HTMLElement>(`[data-testid="${idFor(name)}"]`);
    // Group controls (segmented, custom) carry the testid on a wrapper —
    // focus the first focusable control inside instead.
    const target =
      el && !el.matches(FOCUSABLE_CONTROL)
        ? (el.querySelector<HTMLElement>(FOCUSABLE_CONTROL) ?? el)
        : el;
    target?.focus();
    target?.scrollIntoView({ block: "center" });
  };

  const handleSubmit = async () => {
    const nextErrors: Record<string, boolean> = {};
    for (const f of visibleFields) {
      if (isRequired(f) && isEmpty(values[f.name])) nextErrors[f.name] = true;
    }
    setErrors(nextErrors);
    const firstInvalid = visibleFields.find((f) => nextErrors[f.name]);
    if (firstInvalid) {
      // The offending field may sit on a tab that is not open — switch to it
      // first, then focus once it has actually rendered.
      if (firstInvalid.tab && firstInvalid.tab !== currentTab) {
        setActiveTab(firstInvalid.tab);
        requestAnimationFrame(() => focusField(firstInvalid.name));
      } else {
        focusField(firstInvalid.name);
      }
      return;
    }

    setSubmitting(true);
    try {
      await onSubmit(values);
    } finally {
      setSubmitting(false);
    }
  };

  const helpFor = (f: FieldDef) =>
    f.help && (
      <HelpPopover title={f.help.title} defaultHint={f.help.defaultHint} testId={`${idFor(f.name)}-help`}>
        {f.help.description}
      </HelpPopover>
    );

  const baseInputClass =
    "w-full rounded-md border bg-surface-subtle px-3 py-1.5 text-sm text-ink placeholder:text-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";

  /** The bare control of a field (no label, hint or error). */
  const renderControl = (f: FieldDef, describedBy?: string) => {
    const id = idFor(f.name);
    const value = values[f.name];
    const invalid = errors[f.name];
    const borderClass = invalid ? "border-escalation" : "border-hairline focus:border-accent";
    // Prose-safe default: proportional UI font. Opt into mono only for code/IDs.
    const fontClass = f.mono ? "font-mono" : "font-sans";

    if (f.type === "text" || f.type === "password") {
      return (
        <input
          id={id}
          data-testid={id}
          type={f.type === "password" ? "password" : "text"}
          maxLength={f.maxLength}
          value={typeof value === "string" ? value : ""}
          placeholder={f.placeholder}
          aria-invalid={invalid || undefined}
          aria-describedby={describedBy}
          onChange={(e) => setField(f.name, e.target.value)}
          className={`${baseInputClass} ${borderClass} ${fontClass}`}
        />
      );
    }
    if (f.type === "number") {
      const numberValue = typeof value === "number" ? value : ((value as string) ?? "");
      const onNumber = (raw: string) => setField(f.name, raw === "" ? "" : Number(raw));
      return f.unit ? (
        <UnitInput
          id={id}
          data-testid={id}
          unit={f.unit}
          min={0}
          value={numberValue}
          placeholder={f.placeholder}
          invalid={invalid}
          aria-describedby={describedBy}
          onChange={(e) => onNumber(e.target.value)}
        />
      ) : (
        <input
          id={id}
          data-testid={id}
          type="number"
          value={numberValue}
          placeholder={f.placeholder}
          aria-invalid={invalid || undefined}
          aria-describedby={describedBy}
          onChange={(e) => onNumber(e.target.value)}
          className={`${baseInputClass} ${borderClass} ${fontClass}`}
        />
      );
    }
    if (f.type === "textarea") {
      return (
        <textarea
          id={id}
          data-testid={id}
          value={typeof value === "string" ? value : ""}
          placeholder={f.placeholder}
          rows={f.rows ?? 4}
          aria-invalid={invalid || undefined}
          aria-describedby={describedBy}
          onChange={(e) => setField(f.name, e.target.value)}
          className={`${baseInputClass} ${borderClass} ${fontClass}`}
        />
      );
    }
    if (f.type === "select") {
      return (
        <SelectMenu
          items={(f.options ?? []).map((o) => ({ value: String(o.value), label: o.label }))}
          value={value == null ? null : String(value)}
          onSelect={(v) => {
            const opt = f.options?.find((o) => String(o.value) === v);
            setField(f.name, opt ? opt.value : v);
          }}
          placeholder={t("admin.form.selectPlaceholder")}
          panelTestId={`${id}-menu`}
          trigger={({ open, ref, toggleProps }) => {
            const selected = f.options?.find((o) => String(o.value) === String(value ?? ""));
            return (
              <button
                ref={ref}
                type="button"
                id={id}
                data-testid={id}
                aria-invalid={invalid || undefined}
                aria-describedby={describedBy}
                {...toggleProps}
                className={`${baseInputClass} ${borderClass} ${fontClass} flex items-center justify-between gap-2 text-left`}
              >
                <span className={cn("truncate", !selected && "text-muted")}>
                  {selected?.label ?? t("admin.form.selectPlaceholder")}
                </span>
                <ChevronDownIcon
                  className={cn(
                    "shrink-0 text-muted transition-transform duration-150",
                    open && "rotate-180",
                  )}
                />
              </button>
            );
          }}
        />
      );
    }
    if (f.type === "segmented") {
      return (
        <SegmentedControl
          id={id}
          testId={id}
          items={f.options ?? []}
          value={value as string | number | null | undefined}
          onChange={(v) => setField(f.name, v)}
          invalid={invalid}
          fullWidth={settings}
          aria-labelledby={`${id}-label`}
          aria-describedby={describedBy}
        />
      );
    }
    if (f.type === "switch") {
      const on = f.switchValues ?? { on: true, off: false };
      const checked = f.switchValues ? String(value) === String(on.on) : Boolean(value);
      return (
        <div className="flex items-center gap-2.5">
          <Switch
            id={id}
            testId={id}
            checked={checked}
            onChange={(c) => setField(f.name, c ? on.on : on.off)}
            aria-describedby={describedBy}
          />
          {f.switchLabels && (
            <span aria-hidden="true" className="text-[12.5px] text-muted">
              {checked ? f.switchLabels.on : f.switchLabels.off}
            </span>
          )}
        </div>
      );
    }
    if (f.type === "custom" && f.render) {
      return f.render(value, (v) => setField(f.name, v), values, {
        setValue: setField,
        idFor,
        invalid: Boolean(invalid),
      });
    }
    return null;
  };

  const afterControlFor = (f: FieldDef) =>
    f.afterControl?.({
      value: values[f.name],
      onChange: (v) => setField(f.name, v),
      values,
      controlId: idFor(f.name),
    });

  const errorFor = (f: FieldDef) =>
    errors[f.name] && <p className="mt-1 text-xs text-escalation">{t("admin.form.required")}</p>;

  const renderField = (f: FieldDef) => {
    const id = idFor(f.name);
    const labelEl = (
      <label
        htmlFor={id}
        id={`${id}-label`}
        className="mb-1 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-muted"
      >
        <span>
          {f.label}
          {isRequired(f) && <span className="text-escalation"> *</span>}
        </span>
        {helpFor(f)}
      </label>
    );

    if (f.type === "section") {
      return (
        <div key={f.name} className="pt-1">
          {f.label && (
            <h3 className="border-b border-hairline pb-1 text-xs font-semibold uppercase tracking-wide text-muted">
              {f.label}
            </h3>
          )}
          {resolveHelpText(f) && <p className="mt-1 text-xs text-muted">{resolveHelpText(f)}</p>}
        </div>
      );
    }

    return (
      <div key={f.name} className="min-w-0">
        {f.type !== "checkbox" && labelEl}
        {renderControl(f)}
        {afterControlFor(f)}
        {resolveHelpText(f) && <p className="mt-1 text-xs text-muted">{resolveHelpText(f)}</p>}
        {errorFor(f)}
      </div>
    );
  };

  const renderCheckbox = (f: FieldDef) => {
    const id = idFor(f.name);
    return (
      <label
        key={f.name}
        htmlFor={id}
        className="flex items-center gap-2 py-0.5 text-sm text-ink"
      >
        <input
          id={id}
          data-testid={id}
          type="checkbox"
          checked={Boolean(values[f.name])}
          onChange={(e) => setField(f.name, e.target.checked)}
          className="h-4 w-4 rounded border-hairline accent-[var(--color-accent,#5B8CFF)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
        />
        <span className="flex items-center gap-1.5">
          {f.label}
          {helpFor(f)}
        </span>
      </label>
    );
  };

  /* ---------- settings appearance ---------- */

  const settingsLabel = (f: FieldDef) => {
    const id = idFor(f.name);
    if (!f.label) return null;
    // Group controls have no labelable element: they point at the label via
    // aria-labelledby, so render it as plain text with an id.
    const labelable = f.type !== "segmented" && f.type !== "custom";
    const content = (
      <>
        <span>
          {f.label}
          {isRequired(f) && <span className="text-escalation"> *</span>}
        </span>
        {helpFor(f)}
      </>
    );
    const cls = "flex items-center gap-1.5 text-[13px] font-medium text-ink";
    return labelable ? (
      <label htmlFor={id} id={`${id}-label`} className={cls}>
        {content}
      </label>
    ) : (
      <div id={`${id}-label`} className={cls}>
        {content}
      </div>
    );
  };

  const settingsHint = (f: FieldDef) => {
    const hint = resolveHelpText(f);
    return hint ? (
      <p id={`${idFor(f.name)}-hint`} className="mt-0.5 text-xs text-muted">
        {hint}
      </p>
    ) : null;
  };

  const renderSettingsField = (f: FieldDef) => {
    const hintId = resolveHelpText(f) ? `${idFor(f.name)}-hint` : undefined;
    const tone = f.rowTone === "subtle" && "bg-surface-subtle last:rounded-b-lg";
    if (f.layout === "block") {
      return (
        <div key={f.name} className={cn("min-w-0", f.label && "px-3.5 py-2.5", tone)}>
          {f.label && <div className="mb-1.5">{settingsLabel(f)}{settingsHint(f)}</div>}
          {renderControl(f, hintId)}
          {afterControlFor(f)}
          {errorFor(f)}
        </div>
      );
    }
    return (
      <div
        key={f.name}
        className={cn(
          "grid gap-x-5 gap-y-1.5 px-3.5 py-2.5 sm:grid-cols-[minmax(0,1fr)_minmax(0,19rem)] sm:items-center",
          tone,
        )}
      >
        <div className="min-w-0">
          {settingsLabel(f)}
          {settingsHint(f)}
        </div>
        <div className="min-w-0">
          {renderControl(f, hintId)}
          {afterControlFor(f)}
          {errorFor(f)}
        </div>
      </div>
    );
  };

  const renderSettingsPair = (pair: [FieldDef, FieldDef]) => (
    <div key={pair[0].name} className="grid gap-x-5 gap-y-2.5 px-3.5 py-2.5 sm:grid-cols-2">
      {pair.map((f) => {
        const hintId = resolveHelpText(f) ? `${idFor(f.name)}-hint` : undefined;
        return (
          <div key={f.name} className="flex min-w-0 flex-col gap-1.5">
            {settingsLabel(f)}
            {renderControl(f, hintId)}
            {settingsHint(f)}
            {afterControlFor(f)}
            {errorFor(f)}
          </div>
        );
      })}
    </div>
  );

  const renderSettings = (list: FieldDef[]) =>
    layoutSettings(list).map((block, idx) => {
      if (block.kind === "section") {
        const f = block.field;
        const hint = resolveHelpText(f);
        return (
          <div key={f.name} className={cn("flex flex-col gap-0.5", idx > 0 && "mt-2")}>
            {f.label && <h3 className="text-[13.5px] font-semibold text-ink">{f.label}</h3>}
            {hint && <p className="text-[12.5px] text-muted">{hint}</p>}
          </div>
        );
      }
      return (
        <div
          key={`group-${idx}`}
          className="flex flex-col divide-y divide-hairline rounded-lg border border-hairline"
        >
          {block.rows.map((row) =>
            row.kind === "pair" ? renderSettingsPair(row.fields) : renderSettingsField(row.field),
          )}
        </div>
      );
    });

  /* ---------- shared body ---------- */

  const renderDefault = (list: FieldDef[]) =>
    layoutRows(list).map((row, idx) =>
      row.kind === "field" ? (
        renderField(row.field)
      ) : row.kind === "pair" ? (
        <div key={row.fields[0].name} className="grid grid-cols-2 gap-3">
          {row.fields.map(renderField)}
        </div>
      ) : (
        <div
          key={`cb-${idx}`}
          className={cn(
            "flex flex-col rounded-md border border-hairline bg-surface-subtle px-3 py-2",
            row.fields.length > 3 && "grid grid-cols-1 gap-x-4 sm:grid-cols-2",
          )}
        >
          {row.fields.map(renderCheckbox)}
        </div>
      ),
    );

  const renderBody = (list: FieldDef[]) => (settings ? renderSettings(list) : renderDefault(list));

  const onTabKeyDown = (e: KeyboardEvent<HTMLButtonElement>, idx: number) => {
    const step = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
    let to: number | null = null;
    if (step !== undefined) to = (idx + step + tabs.length) % tabs.length;
    else if (e.key === "Home") to = 0;
    else if (e.key === "End") to = tabs.length - 1;
    if (to === null) return;
    e.preventDefault();
    setActiveTab(tabs[to]);
    tabRefs.current[to]?.focus();
  };

  const formId = `${testIdPrefix}-form`;
  const currentIdx = currentTab === null ? -1 : tabs.indexOf(currentTab);
  const status = footerStatus?.(values);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      description={description}
      size={size}
      footer={
        <>
          {submitError ? (
            <p
              className="mr-auto text-sm text-escalation"
              data-testid={`${testIdPrefix}-error`}
            >
              {submitError}
            </p>
          ) : (
            status != null && (
              <p
                className="mr-auto text-[12.5px] text-muted"
                aria-live="polite"
                data-testid={`${testIdPrefix}-status`}
              >
                {status}
              </p>
            )
          )}
          <Button type="button" variant="ghost" onClick={onClose}>
            {t("admin.form.cancel")}
          </Button>
          <Button
            type="submit"
            form={formId}
            variant="primary"
            disabled={submitting}
            data-testid={`${testIdPrefix}-submit`}
          >
            {submitting ? t("admin.form.saving") : t("admin.form.save")}
          </Button>
        </>
      }
    >
      <form
        id={formId}
        ref={formRef}
        data-testid={testIdPrefix}
        onSubmit={(e) => {
          e.preventDefault();
          void handleSubmit();
        }}
        onKeyDown={(e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
            e.preventDefault();
            void handleSubmit();
          }
        }}
        className="flex flex-col gap-3"
      >
        {tabbed && (
          <div
            role="tablist"
            aria-label={title}
            className={cn(
              "flex gap-0.5 border-b border-hairline",
              // Settings: flush under the dialog header and pinned while the
              // body scrolls. Cancels the Dialog body padding; sticky insets
              // count from the padded edge, hence -top-3 rather than top-0.
              settings
                ? "sticky -top-3 z-10 -mx-4 -mt-3 overflow-x-auto bg-surface px-3"
                : "-mt-1",
            )}
          >
            {tabs.map((tab, tabIdx) => {
              const missing = missingIn(tab);
              const on = tab === currentTab;
              return (
                <button
                  key={tab}
                  ref={(el) => {
                    tabRefs.current[tabIdx] = el;
                  }}
                  type="button"
                  role="tab"
                  id={tabId(tabIdx)}
                  aria-selected={on}
                  aria-controls={panelId(tabIdx)}
                  tabIndex={on ? 0 : -1}
                  // Index-based: the label is a translated string, so a
                  // label-derived testid would change with the UI language.
                  data-testid={`${testIdPrefix}-tab-${tabIdx}`}
                  onClick={() => setActiveTab(tab)}
                  onKeyDown={(e) => onTabKeyDown(e, tabIdx)}
                  className={cn(
                    "-mb-px flex items-center gap-1.5 whitespace-nowrap border-b-2 transition-colors duration-100",
                    "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
                    settings ? "px-2.5 pb-2 pt-2.5 text-[13px]" : "px-2.5 py-1.5 text-sm",
                    on
                      ? "border-accent font-semibold text-accent"
                      : "border-transparent font-medium text-muted hover:text-ink",
                  )}
                >
                  {tab}
                  {/* Only on tabs you cannot see — on the open tab the empty
                      required field is right there, and the badge is noise. */}
                  {!on && missing > 0 && (
                    <span
                      aria-label={t("admin.form.required")}
                      className="flex h-4 min-w-4 items-center justify-center rounded-full bg-escalation px-1 text-[10px] font-bold text-white"
                    >
                      {missing}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        )}
        {tabbed && stableTabHeight ? (
          // Every tab in the same grid cell: the cell is as tall as the
          // tallest tab, only the active one is visible/focusable.
          <div className="grid min-w-0">
            {tabs.map((tab, tabIdx) => {
              const on = tab === currentTab;
              return (
                <div
                  key={tab}
                  role="tabpanel"
                  id={panelId(tabIdx)}
                  aria-labelledby={tabId(tabIdx)}
                  aria-hidden={on ? undefined : true}
                  inert={!on}
                  data-testid={`${testIdPrefix}-tabpanel-${tabIdx}`}
                  className={cn(
                    "col-start-1 row-start-1 flex min-w-0 flex-col gap-3",
                    !on && "invisible",
                  )}
                >
                  {renderBody(fieldsInTab(tab))}
                </div>
              );
            })}
          </div>
        ) : tabbed ? (
          <div
            role="tabpanel"
            id={panelId(currentIdx)}
            aria-labelledby={tabId(currentIdx)}
            data-testid={`${testIdPrefix}-tabpanel-${currentIdx}`}
            className="flex min-w-0 flex-col gap-3"
          >
            {renderBody(shownFields)}
          </div>
        ) : (
          renderBody(shownFields)
        )}
      </form>
    </Dialog>
  );
}
