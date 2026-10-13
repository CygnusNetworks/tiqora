import { useId, useRef, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { FieldInput } from "@/components/fields/FieldInput";
import type { FieldOption } from "@/components/fields/types";
import { Button } from "@/components/ui/Button";
import type { AttributeDef, CmdbAttachment, FormOptions } from "@/lib/cmdbApi";
import {
  addEntry,
  fromFieldValue,
  pathString,
  removeEntry,
  setValue,
  toFieldDef,
  toFieldValue,
  type AttrValues,
  type EntryPath,
} from "./attributeModel";

type Ctx = {
  values: AttrValues;
  onChange: (next: AttrValues) => void;
  options: FormOptions;
  errors: Record<string, string>;
  disabled?: boolean;
  attachments: CmdbAttachment[];
  onUpload?: (file: File) => Promise<CmdbAttachment | void> | void;
  onRemoveAttachment?: (id: number) => Promise<void> | void;
  idBase: string;
};

const slug = (path: string) => path.replace(/[^A-Za-z0-9]+/g, "-").replace(/-$/, "");

function entriesOf(values: AttrValues, parents: EntryPath, key: string) {
  let level: AttrValues | undefined = values;
  for (const p of parents) level = level?.[p.key]?.[p.index]?.s;
  return level?.[key] ?? [];
}

/**
 * Attribute editor of a CI: one labelled block per attribute, repeatable entries within
 * `count_min..count_max`, sub attributes nested under their entry, groups as fieldsets. It fetches
 * nothing itself; `options` come from `GET /cmdb/form-options`, the reference kinds look themselves
 * up. `errors` are keyed by the API's issue paths (`NIC[1].IPAddress[2]`); a value starting with
 * `cmdb.validation.` is translated, anything else is shown as sent.
 */
export function CiAttributeForm({
  defs,
  values,
  onChange,
  options,
  errors,
  disabled,
  attachments,
  onUpload,
  onRemoveAttachment,
}: {
  defs: AttributeDef[];
  values: AttrValues;
  onChange: (next: AttrValues) => void;
  options: FormOptions;
  errors?: Record<string, string>;
  disabled?: boolean;
  attachments?: CmdbAttachment[];
  onUpload?: (file: File) => Promise<CmdbAttachment | void> | void;
  /** Called after an attachment value was cleared with "Entfernen" (the page decides whether the file goes). */
  onRemoveAttachment?: (id: number) => Promise<void> | void;
}) {
  const idBase = useId();
  const ctx: Ctx = {
    values,
    onChange,
    options,
    errors: errors ?? {},
    disabled,
    attachments: attachments ?? [],
    onUpload,
    onRemoveAttachment,
    idBase,
  };
  return (
    <div className="space-y-4" data-testid="ci-attr-form">
      {defs.map((d) => (
        <AttributeBlock key={d.key} def={d} parents={[]} prefix="" ctx={ctx} />
      ))}
    </div>
  );
}

function ErrorLine({ id, message }: { id: string; message: string | undefined }) {
  const { t } = useTranslation();
  if (!message) return null;
  return (
    <p id={id} data-testid={id} role="alert" className="mt-1 text-xs text-danger">
      {message.startsWith("cmdb.validation.") ? t(message) : message}
    </p>
  );
}

function AttributeBlock({ def, parents, prefix, ctx }: { def: AttributeDef; parents: EntryPath; prefix: string; ctx: Ctx }) {
  const { t } = useTranslation();
  const entries = entriesOf(ctx.values, parents, def.key);
  const listPath = `${prefix}${def.key}`;
  const listErrorId = `${ctx.idBase}-${slug(listPath)}-error`;
  const multi = (def.count_max ?? 1) > 1;
  const canAdd = entries.length < (def.count_max ?? 1);
  const canRemove = entries.length > (def.count_min ?? 0);
  const isGroup = def.type === "group";
  const subs = def.sub ?? [];

  function update(next: AttrValues) {
    ctx.onChange(next);
  }

  const entryNodes = entries.map((_, i) => {
    const path: EntryPath = [...parents, { key: def.key, index: i }];
    const epath = pathString(path);
    const eslug = slug(epath);
    const controlId = `${ctx.idBase}-${eslug}`;
    const errorId = `${controlId}-error`;
    const error = ctx.errors[epath];
    const helpId = `${controlId}-help`;
    const describedBy = error ? errorId : def.help && !multi ? helpId : undefined;
    const remove = multi ? (
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="shrink-0"
        disabled={ctx.disabled || !canRemove}
        data-testid={`ci-attr-${eslug}-remove`}
        aria-label={`${t("cmdb.form.removeEntry")}: ${def.name} ${i + 1}`}
        onClick={() => update(removeEntry(ctx.values, path))}
      >
        {t("cmdb.form.removeEntry")}
      </Button>
    ) : null;
    const entry = entries[i];
    const nested: ReactNode =
      subs.length > 0 ? (
        <div className="mt-2 space-y-3 border-l border-hairline pl-4" data-testid={`ci-attr-${eslug}-sub`}>
          {subs.map((sd) => (
            <AttributeBlock key={sd.key} def={sd} parents={path} prefix={`${epath}.`} ctx={ctx} />
          ))}
        </div>
      ) : null;

    if (isGroup) {
      return (
        <fieldset key={i} className="rounded border border-hairline p-3" data-testid={`ci-attr-${eslug}`}>
          <legend className="px-1 text-xs font-medium text-muted">
            {def.name}
            {multi ? ` ${i + 1}` : ""}
          </legend>
          {nested}
          <div className="mt-2 flex items-center gap-2">
            {remove}
          </div>
          <ErrorLine id={errorId} message={error} />
        </fieldset>
      );
    }

    return (
      <div key={i} data-testid={`ci-attr-${eslug}`}>
        <div className="flex items-start gap-2">
          <div className="min-w-0 flex-1">
            {def.type === "attachment" ? (
              <AttachmentControl
                def={def}
                value={entry.v}
                onValue={(v) => update(setValue(ctx.values, path, v))}
                ctx={ctx}
                id={controlId}
                testId={`ci-attr-${eslug}-input`}
                invalid={!!error}
                describedBy={describedBy}
              />
            ) : (
              <EntryField
                def={def}
                value={entry.v}
                onValue={(v) => update(setValue(ctx.values, path, v))}
                ctx={ctx}
                id={controlId}
                testId={`ci-attr-${eslug}-input`}
                invalid={!!error}
                describedBy={describedBy}
              />
            )}
          </div>
          {remove}
        </div>
        <ErrorLine id={errorId} message={error} />
        {nested}
      </div>
    );
  });

  const body = (
    <div className="space-y-2">
      {entryNodes}
      {(multi || entries.length === 0) && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={ctx.disabled || !canAdd}
          data-testid={`ci-attr-${slug(listPath)}-add`}
          onClick={() => update(addEntry(ctx.values, [...parents, { key: def.key, index: entries.length }], def))}
        >
          {t("cmdb.form.addEntry")}
        </Button>
      )}
      <ErrorLine id={listErrorId} message={ctx.errors[listPath]} />
    </div>
  );

  if (isGroup) {
    return <div data-testid={`ci-attr-block-${slug(listPath)}`}>{body}</div>;
  }

  const labelId = `${ctx.idBase}-${slug(listPath)}-label`;
  return (
    <div role="group" aria-labelledby={labelId} data-testid={`ci-attr-block-${slug(listPath)}`}>
      <div id={labelId} className="mb-1 text-xs font-medium text-muted">
        {def.name}
        {def.required && (
          <span className="ml-0.5 text-danger">
            <span aria-hidden="true">*</span>
            <span className="sr-only"> ({t("fields.required")})</span>
          </span>
        )}
      </div>
      {def.help && !multi && (
        <p id={`${ctx.idBase}-${slug(`${listPath}[1]`)}-help`} className="mb-1 text-xs text-muted">
          {def.help}
        </p>
      )}
      {def.help && multi && <p className="mb-1 text-xs text-muted">{def.help}</p>}
      {body}
    </div>
  );
}

function EntryField({
  def,
  value,
  onValue,
  ctx,
  id,
  testId,
  invalid,
  describedBy,
}: {
  def: AttributeDef;
  value: AttrValues[string][number]["v"];
  onValue: (v: AttrValues[string][number]["v"]) => void;
  ctx: Ctx;
  id: string;
  testId: string;
  invalid: boolean;
  describedBy?: string;
}) {
  const { t } = useTranslation();
  const fd = toFieldDef(def, t, ctx.options);
  return (
    <FieldInput
      def={{ ...fd, label: def.name }}
      id={id}
      testId={testId}
      value={toFieldValue(def, value)}
      onChange={(v) => onValue(fromFieldValue(def, v))}
      options={fd.options}
      disabled={ctx.disabled}
      invalid={invalid}
      describedBy={describedBy}
    />
  );
}

/** Attachment attribute: pick one of the CI's attachments, or upload a new one and select it. */
function AttachmentControl({
  def,
  value,
  onValue,
  ctx,
  id,
  testId,
  invalid,
  describedBy,
}: {
  def: AttributeDef;
  value: AttrValues[string][number]["v"];
  onValue: (v: AttrValues[string][number]["v"]) => void;
  ctx: Ctx;
  id: string;
  testId: string;
  invalid: boolean;
  describedBy?: string;
}) {
  const { t } = useTranslation();
  const fileRef = useRef<HTMLInputElement>(null);
  const items: FieldOption[] = ctx.attachments.map((a) => ({ value: String(a.id), label: a.filename }));
  const fd = toFieldDef(def, t, ctx.options);
  return (
    <div className="flex items-center gap-2">
      <div className="min-w-0 flex-1">
        <FieldInput
          def={{ ...fd, label: def.name, options: items }}
          id={id}
          testId={testId}
          value={toFieldValue(def, value)}
          onChange={(v) => onValue(fromFieldValue(def, v))}
          options={items}
          disabled={ctx.disabled}
          invalid={invalid}
          describedBy={describedBy}
        />
      </div>
      {ctx.onUpload && (
        <>
          <input
            ref={fileRef}
            type="file"
            className="sr-only"
            tabIndex={-1}
            aria-hidden="true"
            data-testid={`${testId}-file`}
            onChange={async (e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (!file) return;
              const made = await ctx.onUpload?.(file);
              if (made && typeof made === "object") onValue(made.id);
            }}
          />
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="shrink-0"
            disabled={ctx.disabled}
            data-testid={`${testId}-upload`}
            onClick={() => fileRef.current?.click()}
          >
            {t("cmdb.form.upload")}
          </Button>
        </>
      )}
      {ctx.onRemoveAttachment && typeof value === "number" && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="shrink-0"
          disabled={ctx.disabled}
          data-testid={`${testId}-remove-file`}
          onClick={() => {
            onValue(null);
            void ctx.onRemoveAttachment?.(value);
          }}
        >
          {t("cmdb.form.removeFile")}
        </Button>
      )}
    </div>
  );
}
