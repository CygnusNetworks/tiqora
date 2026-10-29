import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { SelectField } from "@/components/ui/SelectField";
import { Switch } from "@/components/ui/Switch";
import { changeAdminApi, changeAdminKeys, type FreezeWindow, type FreezeWindowIn } from "@/lib/changeAdminApi";
import { changeKeys } from "@/lib/changeApi";
import { changeAdminErrorText, intOrNull } from "./changeAdminUi";
import { FIELD_CLASS, fromLocalInput, LABEL_CLASS, toLocalInput } from "./changeUi";
import { ServiceChipsField } from "./ServiceChipsField";

/** Recurrence types of `tiqora.calendar.schemas.RecurrenceIn` (labels from the agent calendar). */
export const RECURRENCE_TYPES = ["Daily", "Weekly", "Monthly", "Yearly"] as const;
type RecurrenceEnd = "never" | "count" | "until";
type Problem = "name" | "window" | "order" | "services" | "interval" | "count" | "until";

type Form = {
  name: string;
  note: string;
  enforcement: FreezeWindow["enforcement"];
  allowEmergency: boolean;
  scope: FreezeWindow["scope"];
  serviceIds: number[];
  start: string;
  end: string;
  recurType: string;
  interval: string;
  recurEnd: RecurrenceEnd;
  count: string;
  until: string;
  valid: boolean;
};

function initialForm(w: FreezeWindow | null): Form {
  const r = w?.recurrence ?? null;
  return {
    name: w?.name ?? "",
    note: w?.note ?? "",
    enforcement: w?.enforcement ?? "block",
    allowEmergency: w?.allow_emergency ?? true,
    scope: w?.scope ?? "all",
    serviceIds: w?.service_ids ?? [],
    start: toLocalInput(w?.starts_at),
    end: toLocalInput(w?.ends_at),
    recurType: r?.type ?? "",
    interval: String(r?.interval ?? 1),
    recurEnd: r?.count != null ? "count" : r?.until ? "until" : "never",
    count: r?.count != null ? String(r.count) : "",
    until: toLocalInput(r?.until),
    valid: w?.valid ?? true,
  };
}

function problemsOf(f: Form): Problem[] {
  const out: Problem[] = [];
  if (f.name.trim() === "") out.push("name");
  if (!f.start || !f.end) out.push("window");
  else if (f.end <= f.start) out.push("order");
  if (f.scope === "services" && f.serviceIds.length === 0) out.push("services");
  if (f.recurType) {
    if ((intOrNull(f.interval) ?? 0) < 1) out.push("interval");
    if (f.recurEnd === "count" && (intOrNull(f.count) ?? 0) < 1) out.push("count");
    if (f.recurEnd === "until" && (!f.until || (f.start && f.until < f.start))) out.push("until");
  }
  return out;
}

function bodyOf(f: Form): FreezeWindowIn {
  return {
    name: f.name.trim(),
    note: f.note.trim() || null,
    enforcement: f.enforcement,
    allow_emergency: f.allowEmergency,
    scope: f.scope,
    service_ids: f.scope === "services" ? f.serviceIds : [],
    starts_at: fromLocalInput(f.start) ?? "",
    ends_at: fromLocalInput(f.end) ?? "",
    recurrence: f.recurType
      ? {
          type: f.recurType,
          interval: intOrNull(f.interval) ?? 1,
          count: f.recurEnd === "count" ? intOrNull(f.count) : null,
          until: f.recurEnd === "until" ? fromLocalInput(f.until) : null,
        }
      : null,
    valid: f.valid,
  };
}


/**
 * Create or edit a freeze window (Sperrzeitraum): effect (block or warn), whether emergency changes
 * pass with a warning, all services or chosen ones, the window in UTC (F35: no time zone, no DST)
 * and an optional recurrence (daily … yearly, interval, end by count or date).
 */
export function FreezeWindowDialog({ window: stored, onClose }: { window: FreezeWindow | null; onClose: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [form, setForm] = useState<Form>(() => initialForm(stored));
  const [touched, setTouched] = useState(false);
  const set = (p: Partial<Form>) => setForm((f) => ({ ...f, ...p }));
  const problems = problemsOf(form);

  const save = useMutation({
    mutationFn: () => {
      const body = bodyOf(form);
      return stored ? changeAdminApi.saveFreezeWindow(stored.id, body) : changeAdminApi.createFreezeWindow(body);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: changeAdminKeys.freezeWindows() });
      void qc.invalidateQueries({ queryKey: changeKeys.all() });
      onClose();
    },
  });
  const submit = () => {
    setTouched(true);
    if (problems.length === 0 && !save.isPending) save.mutate();
  };

  return (
    <Dialog
      open
      onClose={onClose}
      size="lg"
      title={stored ? t("admin.itsmChange.freeze.edit") : t("admin.itsmChange.freeze.new")}
      footer={
        <>
          <Button onClick={onClose} data-testid="freeze-cancel">
            {t("common.cancel")}
          </Button>
          <Button variant="primary" disabled={save.isPending} onClick={submit} data-testid="freeze-save">
            {t("common.save")}
          </Button>
        </>
      }
    >
      <div className="space-y-4 text-sm" data-testid="freeze-dialog">
        <p className="text-xs text-muted">{t("admin.itsmChange.freeze.utc")}</p>
        <label className="block">
          <span className={LABEL_CLASS}>{t("admin.itsmChange.freeze.name")}</span>
          <input className={FIELD_CLASS} value={form.name} maxLength={200} onChange={(e) => set({ name: e.target.value })} aria-invalid={touched && problems.includes("name")} data-testid="freeze-name" />
        </label>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className="block min-w-0">
            <span className={LABEL_CLASS}>{t("admin.itsmChange.freeze.start")}</span>
            <input type="datetime-local" className={FIELD_CLASS} value={form.start} onChange={(e) => set({ start: e.target.value })} aria-invalid={touched && (problems.includes("window") || problems.includes("order"))} data-testid="freeze-start" />
          </label>
          <label className="block min-w-0">
            <span className={LABEL_CLASS}>{t("admin.itsmChange.freeze.end")}</span>
            <input type="datetime-local" className={FIELD_CLASS} value={form.end} onChange={(e) => set({ end: e.target.value })} aria-invalid={touched && (problems.includes("window") || problems.includes("order"))} data-testid="freeze-end" />
          </label>
        </div>

        <div>
          <span className={LABEL_CLASS}>{t("admin.itsmChange.freeze.enforcementLabel")}</span>
          <SegmentedControl
            items={(["block", "warn"] as const).map((v) => ({ value: v, label: t(`admin.itsmChange.freeze.enforcement.${v}`) }))}
            value={form.enforcement}
            onChange={(enforcement) => set({ enforcement })}
            testId="freeze-enforcement"
            aria-label={t("admin.itsmChange.freeze.enforcementLabel")}
          />
        </div>
        <label className="flex items-center gap-2 text-ink">
          <Switch checked={form.allowEmergency} onChange={(allowEmergency) => set({ allowEmergency })} testId="freeze-allow-emergency" aria-label={t("admin.itsmChange.freeze.allowEmergency")} />
          {t("admin.itsmChange.freeze.allowEmergency")}
        </label>

        <div className="space-y-2">
          <span className={LABEL_CLASS}>{t("admin.itsmChange.freeze.scopeLabel")}</span>
          <SegmentedControl
            items={(["all", "services"] as const).map((v) => ({ value: v, label: t(`admin.itsmChange.freeze.scope.${v}`) }))}
            value={form.scope}
            onChange={(scope) => set({ scope })}
            testId="freeze-scope"
            aria-label={t("admin.itsmChange.freeze.scopeLabel")}
          />
          {form.scope === "services" && (
            <ServiceChipsField
              value={form.serviceIds}
              onChange={(serviceIds) => set({ serviceIds })}
              testId="freeze-service"
              label={t("admin.itsmChange.editor.services")}
              invalid={touched && problems.includes("services")}
            />
          )}
        </div>

        <fieldset className="space-y-2 rounded-md border border-hairline p-3">
          <legend className="px-1 text-xs font-medium text-muted">{t("admin.itsmChange.freeze.recurrence")}</legend>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="min-w-0">
              <SelectField
                items={[
                  { value: "", label: t("admin.itsmChange.freeze.recurrenceNone") },
                  ...RECURRENCE_TYPES.map((r) => ({ value: r as string, label: t(`calendar.recurrence.${r}`) })),
                ]}
                value={form.recurType}
                onChange={(recurType) => set({ recurType })}
                testId="freeze-recurrence-type"
                aria-label={t("admin.itsmChange.freeze.recurrence")}
              />
            </div>
            {form.recurType && (
              <label className="block min-w-0">
                <span className={LABEL_CLASS}>{t("admin.itsmChange.freeze.interval")}</span>
                <input type="number" min={1} className={FIELD_CLASS} value={form.interval} onChange={(e) => set({ interval: e.target.value })} aria-invalid={touched && problems.includes("interval")} data-testid="freeze-recurrence-interval" />
              </label>
            )}
          </div>
          {form.recurType && (
            <div className="space-y-2">
              <span className={LABEL_CLASS}>{t("admin.itsmChange.freeze.recurrenceEnd")}</span>
              <SegmentedControl
                items={[
                  { value: "never" as const, label: t("admin.itsmChange.freeze.endNever") },
                  { value: "count" as const, label: t("admin.itsmChange.freeze.endCount") },
                  { value: "until" as const, label: t("admin.itsmChange.freeze.endUntil") },
                ]}
                value={form.recurEnd}
                onChange={(recurEnd) => set({ recurEnd })}
                testId="freeze-recurrence-end"
                aria-label={t("admin.itsmChange.freeze.recurrenceEnd")}
              />
              {form.recurEnd === "count" && (
                <label className="block">
                  <span className={LABEL_CLASS}>{t("admin.itsmChange.freeze.count")}</span>
                  <input type="number" min={1} className={FIELD_CLASS} value={form.count} onChange={(e) => set({ count: e.target.value })} aria-invalid={touched && problems.includes("count")} data-testid="freeze-recurrence-count" />
                </label>
              )}
              {form.recurEnd === "until" && (
                <label className="block">
                  <span className={LABEL_CLASS}>{t("admin.itsmChange.freeze.until")}</span>
                  <input type="datetime-local" className={FIELD_CLASS} value={form.until} onChange={(e) => set({ until: e.target.value })} aria-invalid={touched && problems.includes("until")} data-testid="freeze-recurrence-until" />
                </label>
              )}
            </div>
          )}
        </fieldset>

        <label className="block">
          <span className={LABEL_CLASS}>{t("admin.itsmChange.freeze.note")}</span>
          <textarea className={FIELD_CLASS} rows={2} maxLength={5000} value={form.note} onChange={(e) => set({ note: e.target.value })} data-testid="freeze-note" />
        </label>
        <label className="flex items-center gap-2 text-ink">
          <Switch checked={form.valid} onChange={(valid) => set({ valid })} testId="freeze-valid" aria-label={t("admin.itsmChange.valid")} />
          {t("admin.itsmChange.valid")}
        </label>

        {touched && problems.length > 0 && (
          <ul className="space-y-0.5 text-xs text-danger" role="alert" data-testid="freeze-problems">
            {problems.map((p) => (
              <li key={p}>{t(`admin.itsmChange.freeze.problems.${p}`)}</li>
            ))}
          </ul>
        )}
        {save.isError && (
          <p className="text-sm text-danger" role="alert" data-testid="freeze-error">
            {changeAdminErrorText(save.error, t)}
          </p>
        )}
      </div>
    </Dialog>
  );
}
