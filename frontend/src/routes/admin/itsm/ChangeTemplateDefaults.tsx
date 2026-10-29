import { useTranslation } from "react-i18next";
import { intOrNull } from "@/components/itsm/change/changeAdminUi";
import { FIELD_CLASS } from "@/components/itsm/change/changeUi";
import { ServiceChipsField } from "@/components/itsm/change/ServiceChipsField";
import { SelectField } from "@/components/ui/SelectField";
import { Switch } from "@/components/ui/Switch";
import type { AdminRiskQuestion } from "@/lib/changeAdminApi";
import type { Defaults } from "./templateDefaults";

type Option = { id: number; name: string };
const LABEL = "mb-1 block text-xs font-medium text-muted";
const PLANS = [
  ["reason", "reason", "template-default-reason"],
  ["implementation_plan", "implementationPlan", "template-default-implementation-plan"],
  ["backout_plan", "backoutPlan", "template-default-backout-plan"],
  ["test_plan", "testPlan", "template-default-test-plan"],
] as const;

/** The defaults a new change takes from the template: title, queue, priority, services, CAB, duration, portal flag, plans, risk answers. */
export function ChangeTemplateDefaults({
  value,
  onChange,
  queues,
  priorities,
  cabs,
  questions,
  questionsFailed,
  durationInvalid,
}: {
  value: Defaults;
  onChange: (next: Defaults) => void;
  queues: Option[];
  priorities: Option[];
  cabs: Option[];
  questions: AdminRiskQuestion[] | undefined;
  questionsFailed: boolean;
  durationInvalid: boolean;
}) {
  const { t } = useTranslation();
  const set = (p: Partial<Defaults>) => onChange({ ...value, ...p });
  const none = { value: 0, label: t("admin.itsmChange.none") };
  const options = (list: Option[]) => [none, ...list.map((o) => ({ value: o.id, label: o.name }))];
  const validQuestions = (questions ?? []).filter((q) => q.valid).sort((a, b) => a.position - b.position);
  const setAnswer = (questionKey: string, answerKey: string) => {
    const next = { ...value.risk_answers };
    if (answerKey) next[questionKey] = answerKey;
    else delete next[questionKey];
    set({ risk_answers: next });
  };

  return (
    <div className="space-y-3 text-sm">
      <label className="block">
        <span className={LABEL}>{t("admin.itsmChange.editor.defaultTitle")}</span>
        <input className={FIELD_CLASS} maxLength={255} value={value.title ?? ""} onChange={(e) => set({ title: e.target.value || null })} data-testid="template-default-title" />
      </label>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div className="min-w-0">
          <span className={LABEL}>{t("admin.itsmChange.editor.queue")}</span>
          <SelectField items={options(queues)} value={value.queue_id ?? 0} onChange={(id) => set({ queue_id: id || null })} testId="template-default-queue" aria-label={t("admin.itsmChange.editor.queue")} />
        </div>
        <div className="min-w-0">
          <span className={LABEL}>{t("admin.itsmChange.editor.priority")}</span>
          <SelectField items={options(priorities)} value={value.priority_id ?? 0} onChange={(id) => set({ priority_id: id || null })} testId="template-default-priority" aria-label={t("admin.itsmChange.editor.priority")} />
        </div>
        <div className="min-w-0">
          <span className={LABEL}>{t("admin.itsmChange.editor.cab")}</span>
          <SelectField items={options(cabs)} value={value.cab_id ?? 0} onChange={(id) => set({ cab_id: id || null })} testId="template-default-cab" aria-label={t("admin.itsmChange.editor.cab")} />
        </div>
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="min-w-0">
          <span className={LABEL}>{t("admin.itsmChange.editor.services")}</span>
          <ServiceChipsField value={value.service_ids} onChange={(service_ids) => set({ service_ids })} testId="template-service" label={t("admin.itsmChange.editor.services")} />
        </div>
        <label className="block min-w-0">
          <span className={LABEL}>{t("admin.itsmChange.editor.duration")}</span>
          <input
            type="number"
            min={1}
            max={525600}
            className={FIELD_CLASS}
            value={value.duration_minutes ?? ""}
            onChange={(e) => set({ duration_minutes: intOrNull(e.target.value) })}
            aria-invalid={durationInvalid}
            data-testid="template-default-duration"
          />
          <span className="mt-1 block text-xs text-muted">{t("admin.itsmChange.editor.durationHint")}</span>
        </label>
      </div>
      <label className="flex items-center gap-2 text-ink">
        <Switch checked={value.portal_visible} onChange={(portal_visible) => set({ portal_visible })} testId="template-default-portal" aria-label={t("admin.itsmChange.editor.portalVisible")} />
        {t("admin.itsmChange.editor.portalVisible")}
      </label>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {PLANS.map(([field, label, testId]) => (
          <label key={field} className="block min-w-0">
            <span className={LABEL}>{t(`admin.itsmChange.editor.${label}`)}</span>
            <textarea className={FIELD_CLASS} rows={3} value={value[field] ?? ""} onChange={(e) => set({ [field]: e.target.value || null })} data-testid={testId} />
          </label>
        ))}
      </div>

      <div className="space-y-2">
        <h3 className="text-sm font-semibold text-ink">{t("admin.itsmChange.editor.riskAnswers")}</h3>
        {questionsFailed ? (
          <p className="text-xs text-danger" role="alert">
            {t("admin.itsmChange.editor.riskAnswersFailed")}
          </p>
        ) : questions && validQuestions.length === 0 ? (
          <p className="text-xs text-muted">{t("admin.itsmChange.editor.riskAnswersEmpty")}</p>
        ) : (
          <ul className="space-y-2">
            {validQuestions.map((q) => (
              <li key={q.id} className="grid grid-cols-1 gap-1 sm:grid-cols-2 sm:items-center sm:gap-3">
                <span className="min-w-0 break-words text-ink">{q.question}</span>
                <SelectField
                  items={[
                    { value: "", label: t("admin.itsmChange.none") },
                    ...q.answers.map((a) => ({ value: a.key, label: t("itsm.change.risk.answer", { label: a.label, count: a.points ?? 0 }) })),
                  ]}
                  value={value.risk_answers[q.question_key] ?? ""}
                  onChange={(answerKey) => setAnswer(q.question_key, answerKey)}
                  testId={`template-risk-${q.question_key}`}
                  aria-label={q.question}
                />
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
