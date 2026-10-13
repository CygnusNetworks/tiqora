import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import type { TicketDetail } from "@/lib/api";
import { phoneApi, type DynamicFieldDef, type DynamicFieldScreen } from "@/lib/phoneApi";
import { apiErrorMessage } from "@/lib/avatarApi";
import { usePatchTicket, ticketPerms } from "@/lib/ticket";
import { displayTimeZone } from "@/lib/timeZone";
import { actionAllowed, type AllowedActions } from "@/lib/aclActions";
import {
  DF_KIND,
  DynamicFieldsForm,
  dfToFieldValue,
  dynamicFieldToDef,
  fieldValueToDf,
  validateFieldValues,
  type DynamicFieldValues,
  type FieldErrors,
  type FieldValues,
} from "@/components/fields";
import { useFieldOptions } from "@/components/fields/useFieldOptions";

type TicketDf = NonNullable<TicketDetail["dynamic_fields"]>[number];

const DEFAULT_SCREEN: DynamicFieldScreen = "AgentTicketFreeText";

/** One value of a dynamic field as plain text (Date / DateTime / Checkbox / Dropdown labels). */
function displayValue(df: TicketDf, def: DynamicFieldDef | undefined, t: TFunction, zone: string): string {
  const fieldType = df.field_type ?? def?.field_type ?? "";
  const kind = DF_KIND[fieldType] ?? "text";
  const values = (df.values ?? []).map(String).filter((v) => v !== "");
  if (values.length === 0) return "—";
  if (kind === "boolean") return values[0] === "1" ? t("ticket.dynamicFieldsEdit.yes") : t("ticket.dynamicFieldsEdit.no");
  if (kind === "date") return values[0].slice(0, 10);
  if (kind === "datetime") {
    const local = dfToFieldValue("datetime", values, zone);
    return typeof local === "string" && local ? local.replace("T", " ") : values[0];
  }
  return values.map((v) => def?.possible_values?.[v] ?? v).join(", ");
}

/**
 * Dynamic fields in the ticket header: read-only values for everybody, plus an
 * edit mode for agents with `rw` — which fields it offers follows Znuny's
 * `Ticket::Frontend::AgentTicketFreeText###DynamicField` (also fields the
 * ticket has no value for yet). Only changed fields are sent.
 */
export function TicketDynamicFields({
  ticket,
  allowedActions = null,
  screen = DEFAULT_SCREEN,
  embedded = false,
  onSaved,
  onCancel,
}: {
  ticket: TicketDetail;
  /** Action ACL result: editing needs the screen's action (`null` = unknown, allowed). */
  allowedActions?: AllowedActions;
  /** Znuny screen whose `###DynamicField` config picks the editable fields (default `AgentTicketFreeText`). */
  screen?: DynamicFieldScreen;
  /** Dialog body: no collapsible shell, opens straight in edit mode (the ITSM field dialogs). */
  embedded?: boolean;
  /** After a successful save or a no-change save in `embedded` mode. */
  onSaved?: () => void;
  /** Cancel pressed in `embedded` mode (closes the dialog). */
  onCancel?: () => void;
}) {
  const SCREEN = screen;
  const { t } = useTranslation();
  const zone = displayTimeZone();
  const canEdit = ticketPerms(ticket).rw && actionAllowed(allowedActions, SCREEN);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<DynamicFieldValues>({});
  const [errors, setErrors] = useState<FieldErrors>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [open, setOpen] = useState(false);
  // Ticket values when editing began: a refetch meanwhile must not make
  // another agent's newer value look like this agent's change.
  const [snapshot, setSnapshot] = useState<DynamicFieldValues>({});

  const defsQ = useQuery({
    queryKey: ["reference", "dynamic-fields", SCREEN],
    queryFn: ({ signal }) => phoneApi.screenDynamicFields(SCREEN, signal),
    staleTime: 5 * 60 * 1000,
  });
  const defs = useMemo(() => defsQ.data ?? [], [defsQ.data]);
  const engineDefs = useMemo(() => defs.map(dynamicFieldToDef), [defs]);
  const defByName = useMemo(() => new Map(defs.map((d) => [d.name, d])), [defs]);

  const ticketValues = useMemo(() => {
    const out: DynamicFieldValues = {};
    for (const df of ticket.dynamic_fields ?? []) out[df.name] = (df.values ?? []).map(String);
    return out;
  }, [ticket.dynamic_fields]);

  /** Ticket values as the editors hand them back, so an untouched field never counts as changed. */
  const baseline = useMemo(() => {
    const out: DynamicFieldValues = {};
    for (const d of engineDefs) {
      out[d.key] = fieldValueToDf(d.kind, dfToFieldValue(d.kind, ticketValues[d.key] ?? [], zone), zone);
    }
    return out;
  }, [engineDefs, ticketValues, zone]);

  const optionKeys = useMemo(
    () => engineDefs.filter((d) => d.optionsKey).map((d) => d.optionsKey as string),
    [engineDefs],
  );
  const optionsByKey = useFieldOptions(
    optionKeys,
    { ticket_id: ticket.id, action: SCREEN, dynamic_fields: draft },
    { enabled: editing },
  );

  const patch = usePatchTicket(ticket.id, () => {
    setEditing(false);
    setSaved(true);
    onSaved?.();
  });

  useEffect(() => {
    if (!saved) return;
    const timer = setTimeout(() => setSaved(false), 4000);
    return () => clearTimeout(timer);
  }, [saved]);

  const startEdit = () => {
    setDraft(baseline);
    setSnapshot(baseline);
    setOpen(true);
    setErrors({});
    setServerError(null);
    setSaved(false);
    setEditing(true);
  };

  const save = () => {
    const engineValues: FieldValues = {};
    for (const d of engineDefs) engineValues[d.key] = dfToFieldValue(d.kind, draft[d.key] ?? [], zone);
    const found = validateFieldValues(engineDefs, engineValues, t);
    setErrors(found);
    setServerError(null);
    if (Object.keys(found).length > 0) return;
    const changed: DynamicFieldValues = {};
    for (const d of engineDefs) {
      const now = draft[d.key] ?? [];
      if (JSON.stringify(now) !== JSON.stringify(snapshot[d.key] ?? [])) changed[d.key] = now;
    }
    if (Object.keys(changed).length === 0) {
      setEditing(false);
      onSaved?.();
      return;
    }
    patch.mutate(
      { dynamic_fields: changed },
      { onError: (err) => setServerError(apiErrorMessage(err, t("ticket.dynamicFieldsEdit.saveError"))) },
    );
  };

  // Dialog mode: go straight into the edit form as soon as the screen's fields are known.
  const autoStarted = useRef(false);
  const readyToEdit = embedded && canEdit && defs.length > 0;
  useEffect(() => {
    if (!readyToEdit || autoStarted.current) return;
    autoStarted.current = true;
    startEdit();
    // startEdit only reads the memoised baseline; running once per dialog is the point.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [readyToEdit]);

  const shown = ticket.dynamic_fields ?? [];
  const showEdit = canEdit && defs.length > 0 && !editing;
  if (shown.length === 0 && !showEdit && !editing) return null;

  const content = (
    <>
      {editing ? (
        <div className="mt-2 space-y-3">
          <DynamicFieldsForm
            testId="ticket-df"
            fields={defs}
            values={draft}
            errors={errors}
            optionsByKey={optionsByKey}
            disabled={patch.isPending}
            onChange={(name, values) => setDraft((d) => ({ ...d, [name]: values }))}
          />
          {serverError && (
            <p role="alert" data-testid="ticket-df-error" className="text-xs text-danger">
              {serverError}
            </p>
          )}
          <div className="flex gap-2">
            <button
              type="button"
              data-testid="ticket-df-save"
              disabled={patch.isPending}
              onClick={save}
              className="rounded bg-accent px-3 py-1 text-xs font-medium text-white disabled:opacity-50"
            >
              {t("ticket.dynamicFieldsEdit.save")}
            </button>
            <button
              type="button"
              data-testid="ticket-df-cancel"
              disabled={patch.isPending}
              onClick={() => {
                setEditing(false);
                setSaved(false);
                onCancel?.();
              }}
              className="rounded border border-hairline px-3 py-1 text-xs"
            >
              {t("ticket.dynamicFieldsEdit.cancel")}
            </button>
          </div>
        </div>
      ) : (
        <>
          <dl className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3">
            {shown.map((df) => (
              <div key={df.name}>
                <dt className="text-xs text-muted">{df.label || df.name}</dt>
                <dd className="text-ink" data-testid={`ticket-df-value-${df.name}`}>
                  {displayValue(df, defByName.get(df.name), t, zone)}
                </dd>
              </div>
            ))}
          </dl>
          <div className="mt-2 flex items-center gap-3">
            {showEdit && (
              <button
                type="button"
                data-testid="ticket-df-edit"
                onClick={startEdit}
                className="rounded border border-hairline px-3 py-1 text-xs"
              >
                {t("ticket.dynamicFieldsEdit.edit")}
              </button>
            )}
            {saved && (
              <span role="status" data-testid="ticket-df-saved" className="text-xs text-muted">
                {t("ticket.dynamicFieldsEdit.saved")}
              </span>
            )}
          </div>
        </>
      )}
    </>
  );

  if (embedded) {
    return (
      <div className="text-sm" data-testid="ticket-df">
        {editing ? content : null}
      </div>
    );
  }

  return (
    <details
      className="rounded border border-hairline bg-surface-subtle px-3 py-2 text-sm"
      open={open}
      onToggle={(e) => setOpen(e.currentTarget.open)}
      data-testid="ticket-df"
    >
      <summary className="cursor-pointer font-medium text-muted">{t("ticket.dynamicFields")}</summary>
      {content}
    </details>
  );
}
