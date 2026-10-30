import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { changeAdminErrorText } from "@/components/itsm/change/changeAdminUi";
import { AdminListState } from "@/components/itsm/change/AdminListState";
import { useDeleteConfirm } from "@/components/itsm/change/useDeleteConfirm";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { changeAdminApi, changeAdminKeys, type AdminRiskQuestion } from "@/lib/changeAdminApi";
import { changeKeys } from "@/lib/changeApi";
import { RiskQuestionDialog } from "./RiskQuestionDialog";

function pointRange(q: AdminRiskQuestion): { min: number; max: number } {
  const points = q.answers.map((a) => a.points ?? 0);
  return { min: Math.min(...points), max: Math.max(...points) };
}

/**
 * The risk questionnaire every change answers: questions in order (move up/down), their answers with
 * points, "Beispielfragen übernehmen" (adds the four example questions whose keys are missing).
 */
export function ChangeRiskQuestionsTab() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [editor, setEditor] = useState<{ question: AdminRiskQuestion | null } | null>(null);
  const q = useQuery({ queryKey: changeAdminKeys.riskQuestions(), queryFn: ({ signal }) => changeAdminApi.riskQuestions(signal) });
  const del = useDeleteConfirm<AdminRiskQuestion>({
    remove: (question) => changeAdminApi.deleteRiskQuestion(question.id),
    invalidate: [changeAdminKeys.riskQuestions(), changeKeys.all()],
    title: t("admin.itsmChange.risk.deleteTitle"),
    message: (question) => t("admin.itsmChange.risk.deleteMessage", { question: question.question }),
    errorTestId: "risk-delete-error",
  });
  const questions = [...(q.data ?? [])].sort((a, b) => a.position - b.position);
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: changeAdminKeys.riskQuestions() });
    void qc.invalidateQueries({ queryKey: changeKeys.all() });
  };

  const reorder = useMutation({
    mutationFn: (order: number[]) => changeAdminApi.orderRiskQuestions(order),
    onSuccess: (data) => {
      qc.setQueryData(changeAdminKeys.riskQuestions(), data);
      void qc.invalidateQueries({ queryKey: changeKeys.all() });
    },
  });
  const move = (i: number, dir: -1 | 1) => {
    const ids = questions.map((x) => x.id);
    const j = i + dir;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    reorder.mutate(ids);
  };
  const examples = useMutation({ mutationFn: () => changeAdminApi.createRiskExamples(), onSuccess: refresh });
  const failure = reorder.error ?? examples.error;

  return (
    <section className="space-y-3" data-testid="change-risk-tab">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="min-w-0 text-sm text-muted">{t("admin.itsmChange.risk.intro")}</p>
        <div className="flex flex-wrap gap-2">
          <Button disabled={examples.isPending} onClick={() => examples.mutate()} data-testid="risk-examples">
            {t("admin.itsmChange.risk.examples")}
          </Button>
          <Button variant="primary" onClick={() => setEditor({ question: null })} data-testid="risk-new">
            {t("admin.itsmChange.risk.new")}
          </Button>
        </div>
      </div>
      {examples.isSuccess && (
        <p className="text-sm text-ink" role="status" data-testid="risk-examples-result">
          {examples.data.created.length > 0
            ? t("admin.itsmChange.risk.examplesDone", { count: examples.data.created.length })
            : t("admin.itsmChange.risk.examplesNone")}
        </p>
      )}
      {failure && (
        <p className="text-sm text-danger" role="alert" data-testid="risk-tab-error">
          {changeAdminErrorText(failure, t)}
        </p>
      )}

      <AdminListState query={q} count={questions.length} emptyText={t("admin.itsmChange.risk.empty")} testId="risk-questions">
          <ol className="divide-y divide-hairline rounded-md border border-hairline bg-surface">
            {questions.map((question, i) => {
              const range = pointRange(question);
              return (
                <li
                  key={question.id}
                  className="flex flex-wrap items-start justify-between gap-2 px-3 py-2"
                  data-testid="risk-question-row"
                  data-question-key={question.question_key}
                >
                  <div className="min-w-0 flex-1 space-y-0.5">
                    <p className="flex flex-wrap items-center gap-1.5 text-sm font-medium text-ink">
                      <span className="min-w-0 break-words">{question.question}</span>
                      {!question.valid && <Badge tone="muted">{t("admin.itsmChange.invalid")}</Badge>}
                    </p>
                    <p className="font-mono text-xs text-muted">{question.question_key}</p>
                    {question.help && <p className="break-words text-xs text-muted">{question.help}</p>}
                    <p className="text-xs text-muted" data-testid="risk-question-answers">
                      {t("admin.itsmChange.risk.answerCount", { count: question.answers.length })} ·{" "}
                      {t("admin.itsmChange.risk.pointRange", range)} ·{" "}
                      {question.answers.map((a) => a.label).join(", ")}
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-1">
                    <Button size="sm" variant="ghost" disabled={i === 0 || reorder.isPending} onClick={() => move(i, -1)} aria-label={t("admin.itsmChange.moveUp")} data-testid={`risk-question-up-${i}`}>
                      ↑
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={i === questions.length - 1 || reorder.isPending}
                      onClick={() => move(i, 1)}
                      aria-label={t("admin.itsmChange.moveDown")}
                      data-testid={`risk-question-down-${i}`}
                    >
                      ↓
                    </Button>
                    <Button size="sm" onClick={() => setEditor({ question })} data-testid="risk-question-edit">
                      {t("common.edit")}
                    </Button>
                    <Button size="sm" variant="danger" onClick={() => del.ask(question)} data-testid="risk-question-delete">
                      {t("common.delete")}
                    </Button>
                  </div>
                </li>
              );
            })}
          </ol>
      </AdminListState>

      {editor && <RiskQuestionDialog key={editor.question?.id ?? "new"} question={editor.question} onClose={() => setEditor(null)} />}
      {del.dialog}
    </section>
  );
}
