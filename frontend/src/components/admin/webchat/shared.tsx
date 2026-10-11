import type { ReactNode } from "react";
import type { Draft, FormErrors } from "@/lib/webchatForm";

export const inputClass =
  "w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink placeholder:text-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent aria-[invalid=true]:border-danger";

export type TabProps = {
  draft: Draft;
  /** Merged client + server errors, keyed by the server's dotted paths. */
  errors: FormErrors;
  onDraft: (next: Draft) => void;
};

/** Label, optional hint and the error of one field (id `webchat-<id>-error`). */
export function Field({
  id,
  label,
  hint,
  error,
  children,
  className,
}: {
  id: string;
  label: string;
  hint?: ReactNode;
  error?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={className} data-testid={`webchat-field-${id}`}>
      <label htmlFor={`webchat-${id}`} className="mb-1 block text-xs font-medium text-muted">
        {label}
      </label>
      {children}
      {hint && !error && <p className="mt-1 text-xs text-muted">{hint}</p>}
      {error && (
        <p role="alert" id={`webchat-${id}-error`} data-testid={`webchat-${id}-error`} className="mt-1 text-xs text-danger">
          {error}
        </p>
      )}
    </div>
  );
}

export function NumberInput({
  id,
  value,
  onChange,
  min,
  max,
  error,
}: {
  id: string;
  value: number;
  onChange: (n: number) => void;
  min: number;
  max: number;
  error?: string;
}) {
  return (
    <input
      id={`webchat-${id}`}
      data-testid={`webchat-${id}`}
      type="number"
      min={min}
      max={max}
      value={Number.isFinite(value) ? value : ""}
      aria-invalid={!!error}
      aria-describedby={error ? `webchat-${id}-error` : undefined}
      onChange={(e) => onChange(e.target.value === "" ? NaN : Number(e.target.value))}
      className={inputClass}
    />
  );
}
