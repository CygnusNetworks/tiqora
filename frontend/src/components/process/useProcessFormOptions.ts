import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { FieldOption, FieldValues } from "@/components/fields";
import { portalProcessApi } from "@/lib/portalProcessApi";
import { processApi, type ProcessDialogFormData } from "@/lib/processApi";
import { displayTimeZone } from "@/lib/timeZone";
import { toFieldOptions, toSubmitValues, type ProcessScope } from "./processForm";

export type ProcessOptionsTarget =
  | { scope: ProcessScope; kind: "start"; processEntityId: string; ticketId?: number }
  | { scope: ProcessScope; kind: "dialog"; ticketId: number; dialogEntityId: string };

const REFRESHABLE = new Set(["select", "multiselect"]);

/**
 * Dependent selects (Znuny's AJAXUpdate): once the value of a field named in a select's `refresh_on`
 * has changed, the server is asked again for that select's options with the current form values,
 * after a short pause so typing does not hammer the API. Nothing is asked at open, so the map stays
 * empty (the fields keep the form's own options) until a source changes. A failed request keeps the
 * options the field already has.
 */
export function useProcessFormOptionsState(
  target: ProcessOptionsTarget | undefined,
  form: ProcessDialogFormData,
  values: FieldValues,
  { debounceMs = 300 }: { debounceMs?: number } = {},
): { options: Record<string, FieldOption[]>; refreshing: boolean } {
  const { t } = useTranslation();
  const [options, setOptions] = useState<Record<string, FieldOption[]>>({});
  const [inFlight, setInFlight] = useState(0);

  const sources = useMemo(() => {
    const names = new Set<string>();
    for (const f of form.fields) {
      if (REFRESHABLE.has(f.kind)) for (const s of f.refresh_on ?? []) names.add(s);
    }
    return [...names].sort();
  }, [form]);

  // What the server would receive, as strings per source; only these decide when to ask again.
  const submit = toSubmitValues(form, values, displayTimeZone(), { scope: target?.scope });
  const snapshot: Record<string, string> = {};
  for (const s of sources) snapshot[s] = JSON.stringify(submit[s] ?? null);
  const snapshotKey = JSON.stringify(snapshot);

  const latest = useRef({ submit, snapshot, target, form });
  latest.current = { submit, snapshot, target, form };

  const [settledKey, setSettledKey] = useState(snapshotKey);
  useEffect(() => {
    if (snapshotKey === settledKey) return;
    const timer = setTimeout(() => setSettledKey(snapshotKey), debounceMs);
    return () => clearTimeout(timer);
  }, [snapshotKey, settledKey, debounceMs]);

  const asked = useRef<Record<string, string>>(snapshot);
  const seq = useRef(0);
  const fieldSeq = useRef<Record<string, number>>({});
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  useEffect(() => {
    const { submit: sent, target: tgt, form: f } = latest.current;
    if (!tgt) return;
    const now = JSON.parse(settledKey) as Record<string, string>;
    const changed = new Set(Object.keys(now).filter((name) => now[name] !== asked.current[name]));
    asked.current = now;
    if (changed.size === 0) return;
    const names = f.fields
      .filter((field) => REFRESHABLE.has(field.kind) && (field.refresh_on ?? []).some((s) => changed.has(s)))
      .map((field) => field.name);
    if (names.length === 0) return;
    const body = { values: sent, fields: names };
    const call =
      tgt.kind === "start"
        ? tgt.scope === "portal"
          ? portalProcessApi.startFormOptions(tgt.processEntityId, body, { ticketId: tgt.ticketId })
          : processApi.startFormOptions(tgt.processEntityId, body, { ticketId: tgt.ticketId })
        : tgt.scope === "portal"
          ? portalProcessApi.dialogFormOptions(tgt.ticketId, tgt.dialogEntityId, body)
          : processApi.dialogFormOptions(tgt.ticketId, tgt.dialogEntityId, body);
    const mine = ++seq.current;
    for (const n of names) fieldSeq.current[n] = mine;
    setInFlight((n) => n + 1);
    call.then(
      (res) => {
        if (!alive.current) return;
        setInFlight((n) => n - 1);
        setOptions((prev) => {
          const next = { ...prev };
          for (const field of f.fields) {
            const fresh = res.options[field.name];
            // a newer request for the same field wins over this late answer
            if (fresh && fieldSeq.current[field.name] === mine) next[field.name] = toFieldOptions(field, fresh, t);
          }
          return next;
        });
      },
      () => {
        if (alive.current) setInFlight((n) => n - 1);
      },
    );
  }, [settledKey, t]);

  return { options, refreshing: inFlight > 0 };
}

/** The refreshed options per field name; plugs into `FieldsForm`'s `optionsByKey`. */
export function useProcessFormOptions(
  target: ProcessOptionsTarget,
  form: ProcessDialogFormData,
  values: FieldValues,
  opts?: { debounceMs?: number },
): Record<string, FieldOption[]> {
  return useProcessFormOptionsState(target, form, values, opts).options;
}
