import { useTranslation } from "react-i18next";
import type { FieldKindProps } from "./types";

/** Znuny's "Attachments" field of a process dialog: the ticket's attachments as checkboxes; the value is the list of option keys. */
export function TicketAttachmentsKind({ def, value, onChange, testId, disabled, invalid, describedBy, labelId, options }: FieldKindProps) {
  const { t } = useTranslation();
  const chosen = Array.isArray(value) ? value : [];
  const locked = disabled || def.readOnly;
  if (options.length === 0) {
    return (
      <p data-testid={testId} className="text-xs text-muted">
        {t("process.form.attachmentsNone")}
      </p>
    );
  }
  return (
    <div
      role="group"
      data-testid={testId}
      aria-labelledby={labelId}
      aria-label={labelId ? undefined : def.label}
      aria-invalid={invalid || undefined}
      aria-describedby={describedBy}
      className="space-y-1"
    >
      {options.map((o, i) => {
        const checked = chosen.includes(o.value);
        return (
          <label key={o.value} className="flex items-start gap-2 text-sm text-ink">
            <input
              type="checkbox"
              className="mt-0.5 shrink-0"
              data-testid={`${testId}-option-${i}`}
              checked={checked}
              disabled={locked || o.disabled}
              onChange={() => onChange(checked ? chosen.filter((k) => k !== o.value) : [...chosen, o.value])}
            />
            <span className="min-w-0 break-words">{o.label}</span>
          </label>
        );
      })}
    </div>
  );
}
