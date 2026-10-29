import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { SelectField } from "@/components/ui/SelectField";
import { Spinner } from "@/components/ui/Spinner";
import { changeApi, changeKeys, type Change, type ChangeRisk, type ChangeRiskOverrideIn } from "@/lib/changeApi";
import { RiskBadge } from "./RiskBadge";
import { RiskFindingList, RiskOverride } from "./ChangeRiskParts";
import { changeErrorText, FINAL_PHASES, SECTION_CLASS, useChangeRefresh, utcText } from "./changeUi";

function Questionnaire({
  risk,
  pending,
  onSave,
}: {
  risk: ChangeRisk;
  pending: boolean;
  onSave: (answers: Record<string, string>) => void;
}) {
  const { t } = useTranslation();
  const [answers, setAnswers] = useState<Record<string, string>>(risk.answers);
  const dirty = JSON.stringify(answers) !== JSON.stringify(risk.answers);
  if (risk.questions.length === 0) return null;
  return (
    <div className="space-y-2" data-testid="change-risk-questionnaire">
      <h4 className="text-xs font-medium text-muted">{t("itsm.change.risk.questionnaire")}</h4>
      <ul className="space-y-2">
        {risk.questions.map((q) => (
          <li key={q.question_key} className="grid gap-1 sm:grid-cols-[1fr_14rem] sm:items-center sm:gap-3">
            <div className="min-w-0 text-xs">
              <p className="break-words text-ink">{q.question}</p>
              {q.help && <p className="break-words text-muted">{q.help}</p>}
            </div>
            <SelectField<string>
              testId={`change-risk-answer-${q.question_key}`}
              aria-label={q.question}
              disabled={!risk.can_answer}
              items={q.answers.map((a) => ({ value: a.key, label: t("itsm.change.risk.answer", { label: a.label, count: a.points }) }))}
              value={answers[q.question_key] ?? null}
              onChange={(value) => setAnswers({ ...answers, [q.question_key]: value })}
              placeholder={t("itsm.change.risk.unanswered")}
            />
          </li>
        ))}
      </ul>
      {!risk.questionnaire_complete && (
        <p className="text-xs text-escalation" data-testid="change-risk-questionnaire-open">
          {t("itsm.change.risk.questionnaireOpen")}
        </p>
      )}
      {risk.can_answer && (
        <div className="flex justify-end">
          <Button size="sm" variant="primary" disabled={!dirty || pending} onClick={() => onSave(answers)} data-testid="change-risk-answers-save">
            {t("itsm.change.risk.saveAnswers")}
          </Button>
        </div>
      )}
    </div>
  );
}

/**
 * "Risikobewertung": the effective level (a manager's override wins), the score against the
 * thresholds, the questionnaire, the findings of the checks and the override. "Neu bewerten"
 * runs the checks again; the impact set they read is recomputed in the impact section.
 */
export function ChangeRiskSection({ change }: { change: Change }) {
  const { t } = useTranslation();
  const id = change.ticket_id;
  const qc = useQueryClient();
  const refresh = useChangeRefresh(id);
  const [saved, setSaved] = useState(0);
  const q = useQuery({ queryKey: changeKeys.risk(id), queryFn: ({ signal }) => changeApi.risk(id, signal) });

  const stored = (risk: ChangeRisk) => {
    qc.setQueryData(changeKeys.risk(id), risk);
    setSaved((n) => n + 1);
    refresh();
  };
  const answers = useMutation({ mutationFn: (a: Record<string, string>) => changeApi.saveRiskAnswers(id, a), onSuccess: stored });
  const evaluate = useMutation({ mutationFn: () => changeApi.evaluateRisk(id), onSuccess: stored });
  const override = useMutation({ mutationFn: (body: ChangeRiskOverrideIn) => changeApi.overrideRisk(id, body), onSuccess: stored });
  const error = answers.error ?? evaluate.error ?? override.error ?? q.error;
  const risk = q.data;
  const canEvaluate = change.can_write && !FINAL_PHASES.has(change.phase);

  return (
    <section className={SECTION_CLASS} aria-label={t("itsm.change.risk.title")} data-testid="change-risk">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-ink">{t("itsm.change.risk.title")}</h3>
        {canEvaluate && risk && (
          <Button size="sm" disabled={evaluate.isPending} onClick={() => evaluate.mutate()} data-testid="change-risk-evaluate">
            {t("itsm.change.risk.evaluate")}
          </Button>
        )}
      </div>
      {error && (
        <p className="text-xs text-danger" role="alert" data-testid="change-risk-error">
          {changeErrorText(error, t)}
        </p>
      )}
      {q.isLoading && <Spinner />}
      {risk && (
        <>
          <div className="space-y-1 text-xs" data-testid="change-risk-summary">
            <p className="flex flex-wrap items-center gap-2">
              <RiskBadge level={risk.effective_level} />
              {risk.score != null && <span className="tabular-nums text-ink">{t("itsm.change.risk.points", { count: risk.score })}</span>}
              {risk.override_level && risk.level && (
                <span className="text-muted">{t("itsm.change.risk.computed", { level: t(`itsm.change.risk.levels.${risk.level}`) })}</span>
              )}
            </p>
            <p className="text-muted">
              {t("itsm.change.risk.thresholds", {
                medium: risk.thresholds.medium_from,
                high: risk.thresholds.high_from,
                veryHigh: risk.thresholds.very_high_from,
              })}
            </p>
            <p className="text-muted">
              {risk.evaluated_at ? t("itsm.change.risk.evaluatedAt", { time: utcText(risk.evaluated_at) }) : t("itsm.change.risk.notEvaluated")}
            </p>
            {risk.blocked && (
              <p className="text-danger" data-testid="change-risk-blocked">
                {t("itsm.change.risk.blocked")}
              </p>
            )}
            {risk.incomplete && risk.evaluated_at && (
              <p className="text-escalation" data-testid="change-risk-incomplete">
                {t("itsm.change.risk.incomplete")}
              </p>
            )}
          </div>
          {/* remounts with the stored answers whenever the server's change (also after another section's write) */}
          <Questionnaire key={`answers-${saved}-${JSON.stringify(risk.answers)}`} risk={risk} pending={answers.isPending} onSave={(a) => answers.mutate(a)} />
          <div className="space-y-1">
            <h4 className="text-xs font-medium text-muted">{t("itsm.change.risk.findingsTitle")}</h4>
            <RiskFindingList findings={risk.findings} />
          </div>
          <RiskOverride key={`override-${saved}`} risk={risk} pending={override.isPending} onSave={(body) => override.mutate(body)} />
        </>
      )}
    </section>
  );
}
