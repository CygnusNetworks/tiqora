import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useRowKeys, newRowKey } from "@/components/approvals/rowKeys";
import { changeAdminErrorText, intOrNull } from "@/components/itsm/change/changeAdminUi";
import { FIELD_CLASS, LABEL_CLASS } from "@/components/itsm/change/changeUi";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Switch } from "@/components/ui/Switch";
import { changeAdminApi, changeAdminKeys, type AdminRiskQuestion } from "@/lib/changeAdminApi";
import { changeKeys } from "@/lib/changeApi";

/** `ChangeRiskQuestionIn.question_key` and `ChangeRiskAnswerDef.key` on the backend. */
export const RISK_KEY_PATTERN = /^[a-z][a-z0-9_]{0,49}$/;
const MIN_ANSWERS = 2;
const MAX_ANSWERS = 10;
const POINTS = [-50, 50] as const;

type AnswerDraft = { key: string; label: string; points: string };
type Problem = "key" | "question" | "answers" | "answerCount";

function answerOk(a: AnswerDraft, all: AnswerDraft[]): boolean {
  const points = intOrNull(a.points);
  return (
    RISK_KEY_PATTERN.test(a.key) &&
    all.filter((b) => b.key === a.key).length === 1 &&
    a.label.trim() !== "" &&
    points != null &&
    points >= POINTS[0] &&
    points <= POINTS[1]
  );
}


/**
 * Create or edit one question of the risk questionnaire: key (fixed once created), question, help text,
 * 2–10 answers with points, valid flag. Answers given by changes are keyed by question and answer key.
 */
export function RiskQuestionDialog({ question, onClose }: { question: AdminRiskQuestion | null; onClose: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [key, setKey] = useState(question?.question_key ?? "");
  const [text, setText] = useState(question?.question ?? "");
  const [help, setHelp] = useState(question?.help ?? "");
  const [valid, setValid] = useState(question?.valid ?? true);
  const [answers, setAnswers] = useState<AnswerDraft[]>(() =>
    question
      ? question.answers.map((a) => ({ key: a.key, label: a.label, points: String(a.points ?? 0) }))
      : [
          { key: "", label: "", points: "0" },
          { key: "", label: "", points: "0" },
        ],
  );
  const [rowKeys, setRowKeys] = useRowKeys(answers.length);
  const [touched, setTouched] = useState(false);

  const problems: Problem[] = [];
  if (!RISK_KEY_PATTERN.test(key)) problems.push("key");
  if (text.trim() === "") problems.push("question");
  if (answers.length < MIN_ANSWERS || answers.length > MAX_ANSWERS) problems.push("answerCount");
  if (answers.some((a) => !answerOk(a, answers))) problems.push("answers");

  const setAnswer = (i: number, p: Partial<AnswerDraft>) => setAnswers((list) => list.map((a, idx) => (idx === i ? { ...a, ...p } : a)));

  const save = useMutation({
    mutationFn: () => {
      const body = {
        question_key: key,
        question: text.trim(),
        help: help.trim() || null,
        answers: answers.map((a) => ({ key: a.key, label: a.label.trim(), points: intOrNull(a.points) ?? 0 })),
        valid,
      };
      return question ? changeAdminApi.saveRiskQuestion(question.id, body) : changeAdminApi.createRiskQuestion(body);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: changeAdminKeys.riskQuestions() });
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
      title={question ? t("admin.itsmChange.risk.edit") : t("admin.itsmChange.risk.new")}
      footer={
        <>
          <Button onClick={onClose} data-testid="risk-cancel">
            {t("common.cancel")}
          </Button>
          <Button variant="primary" disabled={save.isPending} onClick={submit} data-testid="risk-save">
            {t("common.save")}
          </Button>
        </>
      }
    >
      <div className="space-y-4 text-sm" data-testid="risk-dialog">
        <label className="block">
          <span className={LABEL_CLASS}>{t("admin.itsmChange.risk.key")}</span>
          <input
            className={`${FIELD_CLASS} font-mono disabled:opacity-60`}
            value={key}
            maxLength={50}
            disabled={question !== null}
            onChange={(e) => setKey(e.target.value)}
            aria-invalid={touched && problems.includes("key")}
            data-testid="risk-key"
          />
          {question && (
            <span className="mt-1 block text-xs text-muted" data-testid="risk-key-hint">
              {t("admin.itsmChange.risk.keyImmutable")}
            </span>
          )}
        </label>
        <label className="block">
          <span className={LABEL_CLASS}>{t("admin.itsmChange.risk.question")}</span>
          <input className={FIELD_CLASS} value={text} maxLength={500} onChange={(e) => setText(e.target.value)} aria-invalid={touched && problems.includes("question")} data-testid="risk-question" />
        </label>
        <label className="block">
          <span className={LABEL_CLASS}>{t("admin.itsmChange.risk.help")}</span>
          <textarea className={FIELD_CLASS} rows={2} maxLength={2000} value={help} onChange={(e) => setHelp(e.target.value)} data-testid="risk-help" />
        </label>

        <div className="space-y-2">
          <h3 className="text-sm font-semibold text-ink">{t("admin.itsmChange.risk.answers")}</h3>
          <p className="text-xs text-muted">{t("admin.itsmChange.risk.answersHint")}</p>
          <ul className="space-y-2">
            {answers.map((a, i) => {
              const n = i + 1;
              const bad = touched && !answerOk(a, answers);
              return (
                <li key={rowKeys[i]} className="flex flex-wrap items-center gap-2 sm:flex-nowrap" data-testid="risk-answer-row">
                  <input
                    className={`${FIELD_CLASS} font-mono sm:w-32 sm:shrink-0`}
                    value={a.key}
                    maxLength={50}
                    placeholder={t("admin.itsmChange.risk.key")}
                    aria-label={t("admin.itsmChange.risk.answerKey", { n })}
                    aria-invalid={bad}
                    onChange={(e) => setAnswer(i, { key: e.target.value })}
                    data-testid={`risk-answer-key-${i}`}
                  />
                  <input
                    className={`${FIELD_CLASS} min-w-0 flex-1`}
                    value={a.label}
                    maxLength={200}
                    aria-label={t("admin.itsmChange.risk.answerLabel", { n })}
                    aria-invalid={bad}
                    onChange={(e) => setAnswer(i, { label: e.target.value })}
                    data-testid={`risk-answer-label-${i}`}
                  />
                  <input
                    type="number"
                    min={POINTS[0]}
                    max={POINTS[1]}
                    className={`${FIELD_CLASS} w-24 shrink-0`}
                    value={a.points}
                    aria-label={t("admin.itsmChange.risk.answerPoints", { n })}
                    aria-invalid={bad}
                    onChange={(e) => setAnswer(i, { points: e.target.value })}
                    data-testid={`risk-answer-points-${i}`}
                  />
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={answers.length <= MIN_ANSWERS}
                    onClick={() => {
                      setRowKeys(rowKeys.filter((_, idx) => idx !== i));
                      setAnswers(answers.filter((_, idx) => idx !== i));
                    }}
                    aria-label={t("admin.itsmChange.risk.removeAnswer", { n })}
                    data-testid={`risk-answer-remove-${i}`}
                  >
                    ✕
                  </Button>
                </li>
              );
            })}
          </ul>
          <Button
            size="sm"
            disabled={answers.length >= MAX_ANSWERS}
            onClick={() => {
              setRowKeys([...rowKeys, newRowKey()]);
              setAnswers([...answers, { key: "", label: "", points: "0" }]);
            }}
            data-testid="risk-answer-add"
          >
            {t("admin.itsmChange.risk.addAnswer")}
          </Button>
        </div>

        <label className="flex items-center gap-2 text-ink">
          <Switch checked={valid} onChange={setValid} testId="risk-valid" aria-label={t("admin.itsmChange.valid")} />
          {t("admin.itsmChange.valid")}
        </label>

        {touched && problems.length > 0 && (
          <ul className="space-y-0.5 text-xs text-danger" role="alert" data-testid="risk-problems">
            {problems.map((p) => (
              <li key={p}>{t(`admin.itsmChange.risk.problems.${p}`)}</li>
            ))}
          </ul>
        )}
        {save.isError && (
          <p className="text-sm text-danger" role="alert" data-testid="risk-error">
            {changeAdminErrorText(save.error, t)}
          </p>
        )}
      </div>
    </Dialog>
  );
}
