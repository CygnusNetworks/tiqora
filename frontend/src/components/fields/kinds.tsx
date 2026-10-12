import { useTranslation } from "react-i18next";
import { SelectField } from "@/components/ui/SelectField";
import { Switch } from "@/components/ui/Switch";
import { TagInput } from "@/components/ui/TagInput";
import { createExtensionPoint } from "@/extensions/registry";
import { cn } from "@/lib/cn";
import { CiRefKind } from "./CiRefKind";
import { CompanyKind } from "./CompanyKind";
import { CustomerKind } from "./CustomerKind";
import { MultiSelectInput } from "./MultiSelectInput";
import { TicketAttachmentsKind } from "./TicketAttachmentsKind";
import type { FieldKindProps, FieldKindRegistration, FieldValue } from "./types";

export const FIELD_KINDS = createExtensionPoint<FieldKindRegistration>("core.fields.kinds", (r) => r.kind);

const INPUT_CLS =
  "w-full rounded border border-hairline bg-surface px-2 py-1 text-sm text-ink focus:outline-none focus:ring-1 focus:ring-accent disabled:cursor-not-allowed disabled:opacity-60";
const INVALID_CLS = "border-danger focus:ring-danger";

const inputCls = (invalid?: boolean) => cn(INPUT_CLS, invalid && INVALID_CLS);

function asString(value: FieldValue): string {
  if (value === null || value === undefined) return "";
  if (Array.isArray(value)) return value.join(", ");
  return String(value);
}

function asList(value: FieldValue): string[] {
  return Array.isArray(value) ? value : [];
}

function nativeInput(type: string) {
  return function NativeInput({ def, value, onChange, id, testId, disabled, invalid, required, describedBy }: FieldKindProps) {
    return (
      <input
        id={id}
        data-testid={testId}
        type={type}
        value={asString(value)}
        disabled={disabled || def.readOnly}
        placeholder={def.placeholder}
        maxLength={def.maxLength}
        aria-invalid={invalid || undefined}
        aria-required={required || undefined}
        aria-describedby={describedBy}
        className={inputCls(invalid)}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  };
}

const TextKind = nativeInput("text");
const DateKind = nativeInput("date");
const DateTimeKind = nativeInput("datetime-local");

function NumberKind({ def, value, onChange, id, testId, disabled, invalid, required, describedBy }: FieldKindProps) {
  return (
    <input
      id={id}
      data-testid={testId}
      type="number"
      value={asString(value)}
      disabled={disabled || def.readOnly}
      placeholder={def.placeholder}
      min={def.min}
      max={def.max}
      step={def.step}
      aria-invalid={invalid || undefined}
      aria-required={required || undefined}
      aria-describedby={describedBy}
      className={inputCls(invalid)}
      onChange={(e) => {
        const raw = e.target.value;
        if (raw === "") return onChange(null);
        const n = Number(raw);
        onChange(Number.isFinite(n) ? n : null);
      }}
    />
  );
}

function TextareaKind({ def, value, onChange, id, testId, disabled, invalid, required, describedBy }: FieldKindProps) {
  return (
    <textarea
      id={id}
      data-testid={testId}
      rows={3}
      value={asString(value)}
      disabled={disabled || def.readOnly}
      placeholder={def.placeholder}
      maxLength={def.maxLength}
      aria-invalid={invalid || undefined}
      aria-required={required || undefined}
      aria-describedby={describedBy}
      className={inputCls(invalid)}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

function BooleanKind({ def, value, onChange, id, testId, disabled, required, describedBy, labelId }: FieldKindProps) {
  return (
    <Switch
      id={id}
      testId={testId}
      checked={value === true}
      disabled={disabled || def.readOnly}
      onChange={onChange}
      aria-labelledby={labelId}
      aria-label={labelId ? undefined : def.label}
      aria-describedby={describedBy}
      aria-required={required}
    />
  );
}

function SelectKind({ def, value, onChange, id, testId, disabled, invalid, describedBy, labelId, options }: FieldKindProps) {
  const { t } = useTranslation();
  const items = def.allowEmpty ? [{ value: "", label: t("fields.none") }, ...options] : options;
  return (
    <SelectField
      items={items}
      value={value === null || value === undefined ? (def.allowEmpty ? "" : null) : String(value)}
      onChange={(v) => onChange(v === "" ? null : v)}
      testId={testId}
      placeholder={def.placeholder}
      disabled={disabled || def.readOnly}
      id={id}
      invalid={invalid}
      aria-describedby={describedBy}
      aria-label={labelId ? undefined : def.label}
      className={invalid ? "border-danger" : undefined}
    />
  );
}

function MultiSelectKind({ def, value, onChange, id, testId, disabled, invalid, required, describedBy, labelId, options }: FieldKindProps) {
  return (
    <MultiSelectInput
      value={asList(value)}
      onChange={onChange}
      options={options}
      testId={testId}
      disabled={disabled || def.readOnly}
      invalid={invalid}
      required={required}
      id={id}
      labelId={labelId}
      label={def.label}
      describedBy={describedBy}
    />
  );
}

function TagsKind({ def, value, onChange, id, testId, disabled, invalid, required, describedBy, options }: FieldKindProps) {
  if (disabled || def.readOnly) {
    return (
      <div data-testid={testId} className="flex flex-wrap gap-1.5">
        {asList(value).map((tag) => (
          <span key={tag} className="rounded-full bg-accent-dim px-2 py-0.5 text-[12px] font-medium text-accent">
            {tag}
          </span>
        ))}
      </div>
    );
  }
  return (
    <TagInput
      value={asList(value)}
      onChange={onChange}
      suggestions={options.map((o) => ({ name: o.value }))}
      placeholder={def.placeholder}
      testId={testId}
      id={id}
      required={required}
      invalid={invalid}
      describedBy={describedBy}
    />
  );
}

/**
 * Secret contract: `null` = untouched (server keeps the stored value),
 * `""` = clear the stored value (only the explicit remove button emits it),
 * any other string = new secret. Erasing typed text goes back to `null`.
 */
function SecretKind({ def, value, onChange, id, testId, disabled, invalid, required, describedBy }: FieldKindProps) {
  const { t } = useTranslation();
  const typed = typeof value === "string" && value !== "";
  const cleared = def.secretSet && value === "";
  const untouchedStored = def.secretSet && (value === null || value === undefined);
  const locked = disabled || def.readOnly;
  return (
    <div className="flex items-center gap-2">
      <input
        id={id}
        data-testid={testId}
        type="password"
        autoComplete="new-password"
        value={typed ? (value as string) : ""}
        disabled={locked}
        placeholder={cleared ? t("fields.secretCleared") : untouchedStored ? t("fields.secretStored") : def.placeholder}
        aria-invalid={invalid || undefined}
        aria-required={required && !def.secretSet ? true : undefined}
        aria-describedby={describedBy}
        className={inputCls(invalid)}
        onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)}
      />
      {untouchedStored && !locked && (
        <button
          type="button"
          data-testid={`${testId}-clear`}
          onClick={() => onChange("")}
          className="shrink-0 rounded border border-hairline px-2 py-1 text-xs text-muted hover:text-ink focus:outline-none focus-visible:ring-1 focus-visible:ring-accent"
        >
          {t("fields.secretRemove")}
        </button>
      )}
      {cleared && !locked && (
        <button
          type="button"
          data-testid={`${testId}-keep`}
          onClick={() => onChange(null)}
          className="shrink-0 rounded border border-hairline px-2 py-1 text-xs text-muted hover:text-ink focus:outline-none focus-visible:ring-1 focus-visible:ring-accent"
        >
          {t("fields.secretKeep")}
        </button>
      )}
    </div>
  );
}

let registered = false;

/** Idempotent: registers the built-in kinds once. */
// eslint-disable-next-line react-refresh/only-export-components -- registry setup is colocated with the kind components
export function registerCoreFieldKinds(): void {
  if (registered && FIELD_KINDS.get("text")) return;
  registered = true;
  const core: FieldKindRegistration[] = [
    { kind: "text", component: TextKind },
    { kind: "textarea", component: TextareaKind },
    { kind: "number", component: NumberKind },
    { kind: "boolean", component: BooleanKind },
    { kind: "select", component: SelectKind },
    { kind: "multiselect", component: MultiSelectKind },
    { kind: "date", component: DateKind },
    { kind: "datetime", component: DateTimeKind },
    { kind: "secret", component: SecretKind },
    { kind: "tags", component: TagsKind },
    { kind: "customer", component: CustomerKind },
    { kind: "ticket_attachments", component: TicketAttachmentsKind },
    { kind: "ci_ref", component: CiRefKind },
    { kind: "company", component: CompanyKind },
  ];
  for (const k of core) if (!FIELD_KINDS.get(k.kind)) FIELD_KINDS.register(k);
}
