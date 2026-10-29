import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { changeApi, type Change, type ChangeUpdateIn } from "@/lib/changeApi";
import {
  changeErrorText,
  FIELD_CLASS,
  FINAL_PHASES,
  fromLocalInput,
  SECTION_CLASS,
  toLocalInput,
  useChangeRefresh,
  utcText,
  windowText,
} from "./changeUi";

const TEXTS = ["reason", "implementation_plan", "backout_plan", "test_plan"] as const;
type TextField = (typeof TEXTS)[number];
type Draft = Record<TextField, string> & { start: string; end: string };

const draftOf = (c: Change): Draft => ({
  reason: c.reason ?? "",
  implementation_plan: c.implementation_plan ?? "",
  backout_plan: c.backout_plan ?? "",
  test_plan: c.test_plan ?? "",
  start: toLocalInput(c.planned_start),
  end: toLocalInput(c.planned_end),
});

/** The PATCH body with only what differs from the stored change; a cleared window is `clear_window`. */
function changes(c: Change, d: Draft): ChangeUpdateIn {
  const out: ChangeUpdateIn = {};
  for (const name of TEXTS) {
    if (d[name].trim() !== (c[name] ?? "").trim()) out[name] = d[name].trim();
  }
  const before = draftOf(c);
  if (d.start !== before.start || d.end !== before.end) {
    if (!d.start && !d.end) out.clear_window = true;
    else {
      out.planned_start = fromLocalInput(d.start);
      out.planned_end = fromLocalInput(d.end);
    }
  }
  return out;
}

function Text({ label, value, testId }: { label: string; value: string | null; testId: string }) {
  const { t } = useTranslation();
  return (
    <div data-testid={testId}>
      <h4 className="text-xs font-medium text-muted">{label}</h4>
      <p className={value ? "whitespace-pre-wrap break-words text-ink" : "text-muted"}>{value || t("itsm.change.plans.empty")}</p>
    </div>
  );
}

/**
 * "Pläne und Zeitfenster": reason, the three plans, the planned window (UTC), the actual times and
 * the review notes. Writers edit everything but the review notes (those come with the step to the
 * review); while the CAB decides, window and plans are frozen (`change_locked_in_approval`).
 */
export function ChangePlansSection({ change }: { change: Change }) {
  const { t } = useTranslation();
  const refresh = useChangeRefresh(change.ticket_id);
  const [draft, setDraft] = useState<Draft | null>(null);
  const final = FINAL_PHASES.has(change.phase);
  const locked = change.phase === "approval";
  const canEdit = change.can_write && !final && !locked;

  const save = useMutation({
    mutationFn: (body: ChangeUpdateIn) => changeApi.update(change.ticket_id, body),
    onSuccess: (c) => {
      setDraft(null);
      refresh(c);
    },
  });

  const halfWindow = !!draft && !draft.start !== !draft.end;
  const backwards = !!draft && !!draft.start && !!draft.end && draft.end <= draft.start;
  const body = draft ? changes(change, draft) : {};
  const label = (name: TextField) => t(`itsm.change.plans.fields.${name}`);

  return (
    <section className={SECTION_CLASS} aria-label={t("itsm.change.plans.title")} data-testid="change-plans">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-ink">{t("itsm.change.plans.title")}</h3>
        {canEdit && !draft && (
          <Button size="sm" onClick={() => setDraft(draftOf(change))} data-testid="change-plans-edit">
            {t("itsm.change.plans.edit")}
          </Button>
        )}
      </div>
      {locked && change.can_write && (
        <p className="text-xs text-muted" data-testid="change-plans-locked">
          {t("itsm.change.plans.locked")}
        </p>
      )}
      {draft ? (
        <div className="space-y-3">
          <div className="grid gap-2 sm:grid-cols-2">
            <label className="block text-xs text-muted">
              {t("itsm.change.plans.start")}
              <input
                type="datetime-local"
                className={`${FIELD_CLASS} mt-1`}
                value={draft.start}
                onChange={(e) => setDraft({ ...draft, start: e.target.value })}
                data-testid="change-plans-start"
              />
            </label>
            <label className="block text-xs text-muted">
              {t("itsm.change.plans.end")}
              <input
                type="datetime-local"
                className={`${FIELD_CLASS} mt-1`}
                value={draft.end}
                onChange={(e) => setDraft({ ...draft, end: e.target.value })}
                data-testid="change-plans-end"
              />
            </label>
          </div>
          {(halfWindow || backwards) && (
            <p className="text-xs text-danger" data-testid="change-plans-window-invalid">
              {t(halfWindow ? "itsm.change.plans.windowHalf" : "itsm.change.errors.invalid_window")}
            </p>
          )}
          {TEXTS.map((name) => (
            <label key={name} className="block text-xs text-muted">
              {label(name)}
              <textarea
                className={`${FIELD_CLASS} mt-1`}
                rows={name === "reason" ? 2 : 4}
                value={draft[name]}
                onChange={(e) => setDraft({ ...draft, [name]: e.target.value })}
                data-testid={`change-plans-input-${name}`}
              />
            </label>
          ))}
          {save.isError && (
            <p className="text-xs text-danger" role="alert" data-testid="change-plans-error">
              {changeErrorText(save.error, t)}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <Button
              size="sm"
              variant="secondary"
              onClick={() => {
                save.reset();
                setDraft(null);
              }}
            >
              {t("common.cancel")}
            </Button>
            <Button
              size="sm"
              variant="primary"
              disabled={save.isPending || halfWindow || backwards || Object.keys(body).length === 0}
              onClick={() => save.mutate(body)}
              data-testid="change-plans-save"
            >
              {t("common.save")}
            </Button>
          </div>
        </div>
      ) : (
        <div className="space-y-2">
          <dl className="grid gap-x-4 gap-y-1 text-xs sm:grid-cols-[auto_1fr]">
            <dt className="text-muted">{t("itsm.change.plans.window")}</dt>
            <dd className="text-ink" data-testid="change-plans-window">
              {windowText(change.planned_start, change.planned_end) || t("itsm.change.list.noWindow")}
            </dd>
            {(change.actual_start || change.actual_end) && (
              <>
                <dt className="text-muted">{t("itsm.change.plans.actual")}</dt>
                <dd className="text-ink" data-testid="change-plans-actual">
                  {windowText(change.actual_start, change.actual_end) || t("itsm.change.plans.since", { time: utcText(change.actual_start ?? change.actual_end) })}
                </dd>
              </>
            )}
          </dl>
          {TEXTS.map((name) => (
            <Text key={name} label={label(name)} value={change[name]} testId={`change-plan-${name}`} />
          ))}
          {change.pir_notes && <Text label={t("itsm.change.plans.fields.pir_notes")} value={change.pir_notes} testId="change-plan-pir_notes" />}
        </div>
      )}
    </section>
  );
}
