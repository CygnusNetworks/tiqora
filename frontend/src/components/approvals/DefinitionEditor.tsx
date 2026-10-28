import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { SelectField } from "@/components/ui/SelectField";
import { Switch } from "@/components/ui/Switch";
import { ApproverEntryEditor } from "./ApproverEntryEditor";
import {
  MAX_APPROVAL_STEPS,
  normalizeStages,
  stepProblems,
  type ApprovalDefinition,
  type ApprovalStepSpec,
  type StepProblem,
} from "./definitionLogic";
import { inputCls } from "./pickers";

type Rule = ApprovalStepSpec["rule"];
type OnTimeout = ApprovalStepSpec["on_timeout"];

const RULES: Rule[] = ["any", "all", "quorum", "majority"];

const blankStep = (stage: number): ApprovalStepSpec => ({
  name: "",
  stage,
  rule: "any",
  quorum: null,
  approvers: [],
  due_hours: null,
  on_timeout: "notify",
  on_no_approver: "reject",
  escalation: [],
  allow_delegation: true,
  allow_email: true,
});

/**
 * Steps of an approval: stages run one after the other, steps of one stage in parallel. Stage numbers
 * stay contiguous (1..k) after every change. Problems are shown inline; `definitionComplete` tells the
 * owner whether it may save. `allowAutoApprove` offers "approve on timeout" (admin templates only).
 */
export function DefinitionEditor({
  value,
  onChange,
  subjectType,
  allowCustomers,
  allowAutoApprove = false,
}: {
  value: ApprovalDefinition;
  onChange: (next: ApprovalDefinition) => void;
  subjectType?: string | null;
  allowCustomers: boolean;
  allowAutoApprove?: boolean;
}) {
  const { t } = useTranslation();
  const steps = value.steps;
  const emit = (next: ApprovalStepSpec[]) => onChange({ ...value, steps: normalizeStages(next) });
  const patch = (i: number, p: Partial<ApprovalStepSpec>) => emit(steps.map((s, idx) => (idx === i ? { ...s, ...p } : s)));
  const maxStage = steps.reduce((m, s) => Math.max(m, s.stage ?? 1), 0);

  const move = (i: number, dir: -1 | 1) => {
    const j = i + dir;
    if (j < 0 || j >= steps.length) return;
    // Neighbours trade places and stage numbers; the stable sort in normalizeStages keeps the result ordered.
    emit(
      steps.map((s, idx) =>
        idx === i ? { ...steps[j], stage: s.stage } : idx === j ? { ...steps[i], stage: s.stage } : s,
      ),
    );
  };

  const timeoutItems: { value: OnTimeout; label: string }[] = (
    ["notify", "escalate", "reject", ...(allowAutoApprove ? ["approve"] : [])] as OnTimeout[]
  ).map((v) => ({ value: v, label: t(`approvals.definition.timeout.${v}`) }));

  return (
    <div className="space-y-3" data-testid="definition-editor">
      {steps.map((step, i) => {
        const problems = stepProblems(step);
        const stageItems = Array.from({ length: maxStage + 1 }, (_, n) => ({
          value: n + 1,
          label: t("approvals.steps.stage", { stage: n + 1 }),
        }));
        const err = (p: StepProblem) =>
          problems.includes(p) && (
            <p className="mt-1 text-xs text-danger" data-testid={`step-${i}-error-${p}`}>
              {t(`approvals.definition.problem.${p}`)}
            </p>
          );
        return (
          <section key={i} className="space-y-3 rounded-md border border-hairline bg-surface p-3" data-testid="definition-step" data-stage={step.stage}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-semibold text-muted" data-testid={`step-${i}-stage-label`}>
                {t("approvals.steps.stage", { stage: step.stage ?? 1 })}
              </span>
              <div className="ml-auto flex gap-1">
                <Button size="sm" variant="ghost" disabled={i === 0} onClick={() => move(i, -1)} aria-label={t("approvals.definition.moveUp")} data-testid={`step-${i}-up`}>
                  ↑
                </Button>
                <Button size="sm" variant="ghost" disabled={i === steps.length - 1} onClick={() => move(i, 1)} aria-label={t("approvals.definition.moveDown")} data-testid={`step-${i}-down`}>
                  ↓
                </Button>
                <Button size="sm" variant="ghost" disabled={steps.length <= 1} onClick={() => emit(steps.filter((_, idx) => idx !== i))} aria-label={t("approvals.definition.removeStep")} data-testid={`step-${i}-remove`}>
                  ✕
                </Button>
              </div>
            </div>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <label className="block text-xs font-medium text-muted">
                {t("approvals.definition.name")}
                <input className={`${inputCls} mt-1 w-full`} value={step.name} maxLength={200} onChange={(e) => patch(i, { name: e.target.value })} data-testid={`step-${i}-name`} />
                {err("name")}
              </label>
              <div className="text-xs font-medium text-muted">
                {t("approvals.definition.stage")}
                <div className="mt-1">
                  <SelectField items={stageItems} value={step.stage ?? 1} onChange={(stage) => patch(i, { stage })} testId={`step-${i}-stage`} aria-label={t("approvals.definition.stage")} />
                </div>
              </div>
            </div>

            <div>
              <div className="mb-1 text-xs font-medium text-muted">{t("approvals.definition.rule")}</div>
              <SegmentedControl<Rule>
                items={RULES.map((r) => ({ value: r, label: t(`approvals.definition.ruleOption.${r}`) }))}
                value={step.rule ?? "any"}
                onChange={(rule) => patch(i, { rule, quorum: rule === "quorum" ? (step.quorum ?? 1) : null })}
                testId={`step-${i}-rule`}
                aria-label={t("approvals.definition.rule")}
              />
              {step.rule === "quorum" && (
                <label className="mt-2 block text-xs font-medium text-muted">
                  {t("approvals.definition.quorum")}
                  <input
                    type="number"
                    min={1}
                    max={100}
                    className={`${inputCls} mt-1 w-28`}
                    value={step.quorum ?? ""}
                    onChange={(e) => patch(i, { quorum: e.target.value === "" ? null : Number(e.target.value) })}
                    data-testid={`step-${i}-quorum`}
                  />
                  {err("quorum")}
                </label>
              )}
            </div>

            <div>
              <div className="mb-1 text-xs font-medium text-muted">{t("approvals.definition.approvers")}</div>
              <ApproverEntryEditor value={step.approvers} onChange={(approvers) => patch(i, { approvers })} subjectType={subjectType} allowCustomers={allowCustomers} />
              {err("approvers")}
            </div>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <label className="block text-xs font-medium text-muted">
                {t("approvals.definition.dueHours")}
                <input
                  type="number"
                  min={0}
                  max={8760}
                  className={`${inputCls} mt-1 w-28`}
                  value={step.due_hours ?? ""}
                  onChange={(e) => patch(i, { due_hours: e.target.value === "" ? null : Number(e.target.value) })}
                  data-testid={`step-${i}-due`}
                />
              </label>
              <div className="text-xs font-medium text-muted">
                {t("approvals.definition.onTimeout")}
                <div className="mt-1">
                  <SelectField items={timeoutItems} value={step.on_timeout ?? "notify"} onChange={(on_timeout) => patch(i, { on_timeout })} testId={`step-${i}-timeout`} aria-label={t("approvals.definition.onTimeout")} />
                </div>
              </div>
            </div>

            {step.on_timeout === "escalate" && (
              <div>
                <div className="mb-1 text-xs font-medium text-muted">{t("approvals.definition.escalation")}</div>
                <ApproverEntryEditor value={step.escalation ?? []} onChange={(escalation) => patch(i, { escalation })} subjectType={subjectType} allowCustomers={allowCustomers} />
                {err("escalation")}
              </div>
            )}

            <div className="flex flex-col gap-2 text-sm text-ink sm:flex-row sm:gap-6">
              <label className="flex items-center gap-2">
                <Switch checked={step.allow_delegation ?? true} onChange={(v) => patch(i, { allow_delegation: v })} testId={`step-${i}-forward`} aria-label={t("approvals.definition.allowForward")} />
                {t("approvals.definition.allowForward")}
              </label>
              <label className="flex items-center gap-2">
                <Switch checked={step.allow_email ?? true} onChange={(v) => patch(i, { allow_email: v })} testId={`step-${i}-email`} aria-label={t("approvals.definition.allowEmail")} />
                {t("approvals.definition.allowEmail")}
              </label>
            </div>
          </section>
        );
      })}
      <Button onClick={() => emit([...steps, blankStep(maxStage + 1)])} disabled={steps.length >= MAX_APPROVAL_STEPS} data-testid="definition-add-step">
        {t("approvals.definition.addStep")}
      </Button>
    </div>
  );
}
