import { useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { HelpPopover } from "@/components/ui/HelpPopover";
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
  | "custom";

export type FieldValues = Record<string, unknown>;

export type FieldDef = {
  name: string;
  label: string;
  type: FieldType;
  required?: boolean;
  options?: FieldOption[];
  placeholder?: string;
  helpText?: string;
  /**
   * When true, render the control in monospace (IDs, code snippets).
   * Default false — prose fields (signatures, templates, comments) use the
   * proportional UI font. Opt in only for genuinely monospaced content.
   */
  mono?: boolean;
  /** Textarea row count (default 4). */
  rows?: number;
  /**
   * Layout width. Short scalar fields (numbers, small selects) read better
   * side by side; adjacent `half` fields share a row. Default "full".
   */
  width?: "full" | "half";
  /** Only for type "custom": renders its own control. */
  render?: (value: unknown, onChange: (v: unknown) => void, values: FieldValues) => ReactNode;
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
  size?: "sm" | "md" | "lg" | "xl";
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
}: CrudDrawerProps) {
  const { t } = useTranslation();
  const [values, setValues] = useState<FieldValues>(initialValues);
  const [errors, setErrors] = useState<Record<string, boolean>>({});
  const [submitting, setSubmitting] = useState(false);
  const formRef = useRef<HTMLFormElement | null>(null);

  useEffect(() => {
    if (open) {
      setValues(initialValues);
      setErrors({});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const setField = (name: string, v: unknown) => {
    setValues((prev) => ({ ...prev, [name]: v }));
    setErrors((prev) => (prev[name] ? { ...prev, [name]: false } : prev));
  };

  const visibleFields = fields.filter((f) => !(mode === "create" && f.hideOnCreate));

  const handleSubmit = async () => {
    const nextErrors: Record<string, boolean> = {};
    for (const f of visibleFields) {
      if (f.required && isEmpty(values[f.name])) nextErrors[f.name] = true;
    }
    setErrors(nextErrors);
    const firstInvalid = visibleFields.find((f) => nextErrors[f.name]);
    if (firstInvalid) {
      const el = formRef.current?.querySelector<HTMLElement>(
        `[data-testid="${testIdPrefix}-${firstInvalid.name}"]`,
      );
      el?.focus();
      el?.scrollIntoView({ block: "center" });
      return;
    }

    setSubmitting(true);
    try {
      await onSubmit(values);
    } finally {
      setSubmitting(false);
    }
  };

  const renderField = (f: FieldDef) => {
    const id = `${testIdPrefix}-${f.name}`;
    const value = values[f.name];
    const invalid = errors[f.name];
    const labelEl = (
      <label
        htmlFor={id}
        className="mb-1 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-muted"
      >
        <span>
          {f.label}
          {f.required && <span className="text-escalation"> *</span>}
        </span>
        {f.help && (
          <HelpPopover title={f.help.title} defaultHint={f.help.defaultHint} testId={`${id}-help`}>
            {f.help.description}
          </HelpPopover>
        )}
      </label>
    );
    const baseInputClass =
      "w-full rounded-md border bg-surface-subtle px-3 py-1.5 text-sm text-ink placeholder:text-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";
    const borderClass = invalid ? "border-escalation" : "border-hairline focus:border-accent";
    // Prose-safe default: proportional UI font. Opt into mono only for code/IDs.
    const fontClass = f.mono ? "font-mono" : "font-sans";

    if (f.type === "section") {
      return (
        <div key={f.name} className="pt-1">
          <h3 className="border-b border-hairline pb-1 text-xs font-semibold uppercase tracking-wide text-muted">
            {f.label}
          </h3>
          {f.helpText && <p className="mt-1 text-xs text-muted">{f.helpText}</p>}
        </div>
      );
    }

    return (
      <div key={f.name} className="min-w-0">
        {f.type !== "checkbox" && labelEl}
        {f.type === "text" || f.type === "password" ? (
          <input
            id={id}
            data-testid={id}
            type={f.type === "password" ? "password" : "text"}
            value={typeof value === "string" ? value : ""}
            placeholder={f.placeholder}
            aria-invalid={invalid || undefined}
            onChange={(e) => setField(f.name, e.target.value)}
            className={`${baseInputClass} ${borderClass} ${fontClass}`}
          />
        ) : f.type === "number" ? (
          <input
            id={id}
            data-testid={id}
            type="number"
            value={typeof value === "number" ? value : ((value as string) ?? "")}
            placeholder={f.placeholder}
            aria-invalid={invalid || undefined}
            onChange={(e) => setField(f.name, e.target.value === "" ? "" : Number(e.target.value))}
            className={`${baseInputClass} ${borderClass} ${fontClass}`}
          />
        ) : f.type === "textarea" ? (
          <textarea
            id={id}
            data-testid={id}
            value={typeof value === "string" ? value : ""}
            placeholder={f.placeholder}
            rows={f.rows ?? 4}
            aria-invalid={invalid || undefined}
            onChange={(e) => setField(f.name, e.target.value)}
            className={`${baseInputClass} ${borderClass} ${fontClass}`}
          />
        ) : f.type === "select" ? (
          <select
            id={id}
            data-testid={id}
            value={value == null ? "" : String(value)}
            aria-invalid={invalid || undefined}
            onChange={(e) => {
              const opt = f.options?.find((o) => String(o.value) === e.target.value);
              setField(f.name, opt ? opt.value : e.target.value);
            }}
            className={`${baseInputClass} ${borderClass} ${fontClass}`}
          >
            <option value="" disabled>
              {t("admin.form.selectPlaceholder")}
            </option>
            {f.options?.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        ) : f.type === "custom" && f.render ? (
          f.render(value, (v) => setField(f.name, v), values)
        ) : null}
        {f.afterControl?.({
          value,
          onChange: (v) => setField(f.name, v),
          values,
          controlId: id,
        })}
        {f.helpText && <p className="mt-1 text-xs text-muted">{f.helpText}</p>}
        {invalid && <p className="mt-1 text-xs text-escalation">{t("admin.form.required")}</p>}
      </div>
    );
  };

  const renderCheckbox = (f: FieldDef) => {
    const id = `${testIdPrefix}-${f.name}`;
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
          {f.help && (
            <HelpPopover
              title={f.help.title}
              defaultHint={f.help.defaultHint}
              testId={`${id}-help`}
            >
              {f.help.description}
            </HelpPopover>
          )}
        </span>
      </label>
    );
  };

  const rows = layoutRows(visibleFields);
  const formId = `${testIdPrefix}-form`;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      description={description}
      size={size}
      footer={
        <>
          {submitError && (
            <p
              className="mr-auto text-sm text-escalation"
              data-testid={`${testIdPrefix}-error`}
            >
              {submitError}
            </p>
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
        {rows.map((row, idx) =>
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
        )}
      </form>
    </Dialog>
  );
}
