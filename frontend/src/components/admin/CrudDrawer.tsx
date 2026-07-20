import { useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";

export type FieldOption = { value: string | number; label: string };

export type FieldType =
  | "text"
  | "textarea"
  | "number"
  | "checkbox"
  | "select"
  | "password"
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
  /** Only for type "custom": renders its own control. */
  render?: (value: unknown, onChange: (v: unknown) => void, values: FieldValues) => ReactNode;
  /** Hide this field for create (e.g. immutable identity fields shown read-only). */
  hideOnCreate?: boolean;
};

export type CrudDrawerProps = {
  open: boolean;
  onClose: () => void;
  title: string;
  fields: FieldDef[];
  initialValues: FieldValues;
  mode: "create" | "edit";
  onSubmit: (values: FieldValues) => Promise<void>;
  submitError?: string | null;
  testIdPrefix?: string;
};

function isEmpty(v: unknown): boolean {
  return v === undefined || v === null || v === "";
}

/**
 * Generic create/edit form host built on the shared Dialog. Column defs stay
 * in the resource page; this only renders inputs from FieldDef[] and does
 * basic required-field validation before calling onSubmit.
 */
export function CrudDrawer({
  open,
  onClose,
  title,
  fields,
  initialValues,
  mode,
  onSubmit,
  submitError,
  testIdPrefix = "admin-form",
}: CrudDrawerProps) {
  const { t } = useTranslation();
  const [values, setValues] = useState<FieldValues>(initialValues);
  const [errors, setErrors] = useState<Record<string, boolean>>({});
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (open) {
      setValues(initialValues);
      setErrors({});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const setField = (name: string, v: unknown) => {
    setValues((prev) => ({ ...prev, [name]: v }));
  };

  const visibleFields = fields.filter((f) => !(mode === "create" && f.hideOnCreate));

  const handleSubmit = async () => {
    const nextErrors: Record<string, boolean> = {};
    for (const f of visibleFields) {
      if (f.required && isEmpty(values[f.name])) nextErrors[f.name] = true;
    }
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;

    setSubmitting(true);
    try {
      await onSubmit(values);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} title={title} className="max-w-lg">
      <form
        data-testid={testIdPrefix}
        onSubmit={(e) => {
          e.preventDefault();
          void handleSubmit();
        }}
        className="flex max-h-[70vh] flex-col gap-3 overflow-y-auto"
      >
        {visibleFields.map((f) => {
          const id = `${testIdPrefix}-${f.name}`;
          const value = values[f.name];
          const invalid = errors[f.name];
          const labelEl = (
            <label
              htmlFor={id}
              className="mb-1 block text-xs font-medium uppercase tracking-wide text-muted"
            >
              {f.label}
              {f.required && <span className="text-escalation"> *</span>}
            </label>
          );
          const baseInputClass =
            "w-full rounded-md border bg-surface-subtle px-3 py-1.5 text-sm text-ink placeholder:text-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";
          const borderClass = invalid ? "border-escalation" : "border-hairline focus:border-accent";
          // Prose-safe default: proportional UI font. Opt into mono only for code/IDs.
          const fontClass = f.mono ? "font-mono" : "font-sans";

          return (
            <div key={f.name}>
              {f.type !== "checkbox" && labelEl}
              {f.type === "text" || f.type === "password" ? (
                <input
                  id={id}
                  data-testid={id}
                  type={f.type === "password" ? "password" : "text"}
                  value={typeof value === "string" ? value : ""}
                  placeholder={f.placeholder}
                  onChange={(e) => setField(f.name, e.target.value)}
                  className={`${baseInputClass} ${borderClass} ${fontClass}`}
                />
              ) : f.type === "number" ? (
                <input
                  id={id}
                  data-testid={id}
                  type="number"
                  value={typeof value === "number" ? value : (value as string) ?? ""}
                  placeholder={f.placeholder}
                  onChange={(e) =>
                    setField(f.name, e.target.value === "" ? "" : Number(e.target.value))
                  }
                  className={`${baseInputClass} ${borderClass} ${fontClass}`}
                />
              ) : f.type === "textarea" ? (
                <textarea
                  id={id}
                  data-testid={id}
                  value={typeof value === "string" ? value : ""}
                  placeholder={f.placeholder}
                  rows={f.rows ?? 4}
                  onChange={(e) => setField(f.name, e.target.value)}
                  className={`${baseInputClass} ${borderClass} ${fontClass}`}
                />
              ) : f.type === "select" ? (
                <select
                  id={id}
                  data-testid={id}
                  value={value == null ? "" : String(value)}
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
              ) : f.type === "checkbox" ? (
                <label htmlFor={id} className="flex items-center gap-2 text-sm text-ink">
                  <input
                    id={id}
                    data-testid={id}
                    type="checkbox"
                    checked={Boolean(value)}
                    onChange={(e) => setField(f.name, e.target.checked)}
                    className="h-4 w-4 rounded border-hairline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
                  />
                  {f.label}
                </label>
              ) : f.type === "custom" && f.render ? (
                f.render(value, (v) => setField(f.name, v), values)
              ) : null}
              {f.helpText && <p className="mt-1 text-xs text-muted">{f.helpText}</p>}
              {invalid && (
                <p className="mt-1 text-xs text-escalation">{t("admin.form.required")}</p>
              )}
            </div>
          );
        })}

        {submitError && (
          <p className="text-sm text-escalation" data-testid={`${testIdPrefix}-error`}>
            {submitError}
          </p>
        )}

        <div className="flex justify-end gap-2 border-t border-hairline pt-3">
          <Button type="button" variant="ghost" onClick={onClose}>
            {t("admin.form.cancel")}
          </Button>
          <Button type="submit" variant="primary" disabled={submitting} data-testid={`${testIdPrefix}-submit`}>
            {submitting ? t("admin.form.saving") : t("admin.form.save")}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
