import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { flattenQueues } from "@/components/agent/QueueTree";
import { changeAdminErrorText } from "@/components/itsm/change/changeAdminUi";
import { FIELD_CLASS } from "@/components/itsm/change/changeUi";
import { TemplateTaskEditor } from "@/components/itsm/change/TemplateTaskEditor";
import { normalizeTask, templateTaskProblems } from "@/components/itsm/change/templateTasks";
import { Button } from "@/components/ui/Button";
import { SelectField } from "@/components/ui/SelectField";
import { Spinner } from "@/components/ui/Spinner";
import { Switch } from "@/components/ui/Switch";
import { api } from "@/lib/api";
import { changeAdminApi, changeAdminKeys, type ChangeTemplate, type ChangeTemplateIn } from "@/lib/changeAdminApi";
import { changeKeys } from "@/lib/changeApi";
import { CHANGE_TYPES } from "@/lib/changePhases";
import { ChangeTemplateDefaults } from "./ChangeTemplateDefaults";
import { fittingRiskAnswers, normalizeDefaults, type Defaults } from "./templateDefaults";

const LABEL = "mb-1 block text-xs font-medium text-muted";
const SECTION = "min-w-0 space-y-3 rounded-lg border border-hairline bg-surface p-4";
type Problem = "nameMissing" | "durationInvalid" | "tasksInvalid";

function TemplateForm({ template }: { template: ChangeTemplate | null }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [name, setName] = useState(template?.name ?? "");
  const [description, setDescription] = useState(template?.description ?? "");
  const [changeType, setChangeType] = useState<ChangeTemplate["change_type"]>(template?.change_type ?? "normal");
  const [isStandard, setIsStandard] = useState(template?.is_standard ?? false);
  const [valid, setValid] = useState(template?.valid ?? true);
  const [defaults, setDefaults] = useState<Defaults>(() => normalizeDefaults(template?.defaults));
  const [tasks, setTasks] = useState(() => (template?.tasks ?? []).map(normalizeTask));
  const [touched, setTouched] = useState(false);

  const queuesQ = useQuery({ queryKey: ["queues"], queryFn: () => api.listQueues() });
  const prioritiesQ = useQuery({ queryKey: ["reference", "priorities"], queryFn: ({ signal }) => api.listReferencePriorities(signal) });
  const cabsQ = useQuery({ queryKey: changeAdminKeys.cabs(), queryFn: ({ signal }) => changeAdminApi.cabs(signal) });
  const questionsQ = useQuery({ queryKey: changeAdminKeys.riskQuestions(), queryFn: ({ signal }) => changeAdminApi.riskQuestions(signal) });
  const queues = flattenQueues(queuesQ.data ?? []).map((q) => ({ id: q.id, name: q.name }));

  const standard = changeType === "standard" && isStandard;
  const duration = defaults.duration_minutes;
  const problems: Problem[] = [];
  if (name.trim() === "") problems.push("nameMissing");
  if (duration != null && (duration < 1 || duration > 525600)) problems.push("durationInvalid");
  if (templateTaskProblems(tasks).some((p) => p.length > 0)) problems.push("tasksInvalid");

  const save = useMutation({
    mutationFn: () => {
      const body: ChangeTemplateIn = {
        name: name.trim(),
        description: description.trim() || null,
        change_type: changeType,
        is_standard: standard,
        valid,
        defaults: { ...defaults, risk_answers: fittingRiskAnswers(defaults.risk_answers, questionsQ.data) },
        tasks: tasks.map(normalizeTask),
      };
      return template ? changeAdminApi.saveTemplate(template.id, body) : changeAdminApi.createTemplate(body);
    },
    onSuccess: (saved) => {
      qc.setQueryData(changeAdminKeys.template(saved.id), saved);
      void qc.invalidateQueries({ queryKey: changeAdminKeys.templates() });
      void qc.invalidateQueries({ queryKey: changeKeys.templates() });
      if (!template) void navigate({ to: "/admin/itsm/change" });
    },
  });
  const submit = () => {
    setTouched(true);
    if (problems.length === 0 && !save.isPending) save.mutate();
  };

  return (
    <>
      <section className={SECTION}>
        <label className="block">
          <span className={LABEL}>{t("admin.itsmChange.editor.name")}</span>
          <input className={FIELD_CLASS} maxLength={200} value={name} onChange={(e) => setName(e.target.value)} aria-invalid={touched && problems.includes("nameMissing")} data-testid="template-name" />
        </label>
        <label className="block">
          <span className={LABEL}>{t("admin.itsmChange.editor.description")}</span>
          <textarea className={FIELD_CLASS} rows={2} maxLength={5000} value={description} onChange={(e) => setDescription(e.target.value)} data-testid="template-description" />
        </label>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="min-w-0">
            <span className={LABEL}>{t("admin.itsmChange.editor.changeType")}</span>
            <SelectField
              items={CHANGE_TYPES.map((v) => ({ value: v, label: t(`itsm.change.types.${v}`) }))}
              value={changeType}
              onChange={setChangeType}
              testId="template-type"
              aria-label={t("admin.itsmChange.editor.changeType")}
            />
          </div>
          <div className="min-w-0 space-y-2 sm:pt-5">
            <label className="flex items-center gap-2 text-sm text-ink">
              <Switch checked={standard} disabled={changeType !== "standard"} onChange={setIsStandard} testId="template-standard" aria-label={t("admin.itsmChange.editor.isStandard")} />
              {t("admin.itsmChange.editor.isStandard")}
            </label>
            <label className="flex items-center gap-2 text-sm text-ink">
              <Switch checked={valid} onChange={setValid} testId="template-valid" aria-label={t("admin.itsmChange.valid")} />
              {t("admin.itsmChange.valid")}
            </label>
          </div>
        </div>
        <p className="text-xs text-muted">{t("admin.itsmChange.editor.isStandardHint")}</p>
      </section>

      <section className={SECTION}>
        <div>
          <h2 className="text-base font-semibold text-ink">{t("admin.itsmChange.editor.defaults")}</h2>
          <p className="text-xs text-muted">{t("admin.itsmChange.editor.defaultsHint")}</p>
        </div>
        <ChangeTemplateDefaults
          value={defaults}
          onChange={setDefaults}
          queues={queues}
          priorities={(prioritiesQ.data ?? []).map((p) => ({ id: p.id, name: p.name }))}
          cabs={(cabsQ.data ?? []).map((c) => ({ id: c.id, name: c.name }))}
          questions={questionsQ.data}
          questionsFailed={questionsQ.isError}
          durationInvalid={touched && problems.includes("durationInvalid")}
        />
      </section>

      <section className={SECTION}>
        <div>
          <h2 className="text-base font-semibold text-ink">{t("admin.itsmChange.editor.tasks")}</h2>
          <p className="text-xs text-muted">{t("admin.itsmChange.editor.tasksHint")}</p>
        </div>
        <TemplateTaskEditor value={tasks} onChange={setTasks} queues={queues} showProblems={touched} />
      </section>

      <div className="flex flex-wrap items-center justify-end gap-3">
        {touched && problems.length > 0 && (
          <ul className="mr-auto space-y-0.5 text-xs text-danger" role="alert" data-testid="template-problems">
            {problems.map((p) => (
              <li key={p}>{t(`admin.itsmChange.editor.${p}`)}</li>
            ))}
          </ul>
        )}
        {save.isError && (
          <p className="mr-auto text-sm text-danger" role="alert" data-testid="template-error">
            {changeAdminErrorText(save.error, t)}
          </p>
        )}
        {save.isSuccess && !save.isPending && template && (
          <p className="mr-auto text-sm text-ink" role="status" data-testid="template-saved">
            {t("admin.itsmChange.editor.saved")}
          </p>
        )}
        <Button variant="primary" disabled={save.isPending} onClick={submit} data-testid="template-save">
          {t("common.save")}
        </Button>
      </div>
    </>
  );
}

/** `/admin/itsm/change/templates/$templateId` (`new` creates): one change template with its defaults and tasks. */
export function ChangeTemplateEditorPage() {
  const { t } = useTranslation();
  const { templateId: raw } = useParams({ strict: false }) as { templateId?: string };
  const isNew = raw === "new";
  const id = Number(raw);
  const known = isNew || (Number.isInteger(id) && id > 0);
  const q = useQuery({
    queryKey: changeAdminKeys.template(id),
    queryFn: ({ signal }) => changeAdminApi.template(id, signal),
    enabled: !isNew && known,
  });

  return (
    <div className="mx-auto max-w-4xl space-y-4 p-4" data-testid="change-template-editor">
      <div className="space-y-1">
        <Link to="/admin/itsm/change" className="text-xs text-accent hover:underline" data-testid="template-back">
          {t("admin.itsmChange.editor.back")}
        </Link>
        <h1 className="font-display text-xl font-semibold text-ink">
          {isNew ? t("admin.itsmChange.editor.titleNew") : t("admin.itsmChange.editor.titleEdit")}
        </h1>
      </div>
      {!known ? (
        <p className="text-sm text-danger" role="alert" data-testid="template-load-error">
          {t("admin.itsmChange.editor.notFound")}
        </p>
      ) : isNew ? (
        <TemplateForm template={null} />
      ) : q.isPending ? (
        <Spinner />
      ) : q.isError ? (
        <p className="text-sm text-danger" role="alert" data-testid="template-load-error">
          {changeAdminErrorText(q.error, t)}
        </p>
      ) : (
        <TemplateForm key={q.data.id} template={q.data} />
      )}
    </div>
  );
}
