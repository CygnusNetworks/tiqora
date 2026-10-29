import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Dialog } from "@/components/ui/Dialog";
import { Switch } from "@/components/ui/Switch";
import { DefinitionEditor } from "@/components/approvals/DefinitionEditor";
import { definitionComplete, type ApprovalDefinition } from "@/components/approvals/definitionLogic";
import { inputCls } from "@/components/approvals/pickers";
import { useDebounced } from "@/components/fields/useDebounced";
import { approvalErrorCode, approvalErrorMessage } from "@/lib/approvalsApi";
import {
  approvalsAdminApi,
  approvalsAdminKeys,
  type ApprovalTemplate,
  type ApprovalTemplateIn,
} from "@/lib/approvalsAdminApi";
import { useSubjectTypes } from "./useSubjectTypes";
import { AdminApprovalsHeader } from "./AdminApprovalsHeader";
import { describeCheck } from "./adminCheck";

const blankDefinition = (): ApprovalDefinition => ({
  steps: [
    {
      name: "",
      stage: 1,
      rule: "any",
      quorum: null,
      approvers: [],
      due_hours: null,
      on_timeout: "notify",
      on_no_approver: "reject",
      escalation: [],
      allow_delegation: true,
      allow_email: true,
    },
  ],
});

function TemplateDialog({ template, onClose }: { template: ApprovalTemplate | null; onClose: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const editing = template !== null;
  const [key, setKey] = useState(template?.template_key ?? "");
  const [name, setName] = useState(template?.name ?? "");
  const [description, setDescription] = useState(template?.description ?? "");
  const [subjectTypes, setSubjectTypes] = useState<string[]>(template?.subject_types ?? []);
  const [touched, setTouched] = useState(false);
  const [keyLeft, setKeyLeft] = useState(false);
  const [discarding, setDiscarding] = useState(false);
  const subjects = useSubjectTypes();
  const [valid, setValid] = useState(template?.valid ?? true);
  const [definition, setDefinition] = useState<ApprovalDefinition>(() =>
    template ? structuredClone(template.definition) : blankDefinition(),
  );

  const initial = useState(() => JSON.stringify([template?.template_key ?? "", template?.name ?? "", template?.description ?? "", template?.subject_types ?? [], template?.valid ?? true, template?.definition ?? blankDefinition()]))[0];
  const dirty = JSON.stringify([key, name, description, subjectTypes, valid, definition]) !== initial;
  const close = () => (dirty ? setDiscarding(true) : onClose());

  // The validate route checks against one subject type (customer approvers depend on it), so the
  // definition is checked once per selected type and the answers are merged; no type = one check without.
  const liveInput = JSON.stringify({ definition, subjects: subjectTypes });
  const checkInput = useDebounced(liveInput, 400);
  const live = liveInput;
  const check = useQuery({
    queryKey: ["admin", "approvals", "validate", checkInput],
    queryFn: async () => {
      const parsed = JSON.parse(checkInput) as { definition: Record<string, unknown>; subjects: string[] };
      const answers = await Promise.all(
        (parsed.subjects.length ? parsed.subjects : [null]).map((subject) =>
          approvalsAdminApi.validateTemplate(parsed.definition, subject),
        ),
      );
      return {
        errors: [...new Set(answers.flatMap((a) => a.errors))],
        warnings: [...new Set(answers.flatMap((a) => a.warnings))],
      };
    },
    staleTime: Infinity,
    gcTime: 0,
  });
  const checkCurrent = checkInput === live && check.isSuccess;
  const errors = checkCurrent ? check.data.errors.map((e) => describeCheck(e, t)) : [];
  // Until the live check is back, show what the server already knew about the stored template.
  const warningCodes = checkCurrent ? check.data.warnings : (template?.warnings ?? []);
  const warnings = warningCodes.map((w) => describeCheck(w, t));

  const keyOk = key.trim() !== "";
  const toggleSubject = (k: string) =>
    setSubjectTypes((cur) => (cur.includes(k) ? cur.filter((x) => x !== k) : [...cur, k]));
  const complete = definitionComplete(definition);
  const canSave = keyOk && name.trim() !== "" && complete && checkCurrent && errors.length === 0;

  const save = useMutation({
    mutationFn: () => {
      const body: ApprovalTemplateIn = {
        template_key: key.trim(),
        name: name.trim(),
        description: description.trim() || null,
        subject_types: subjectTypes,
        definition,
        valid,
        // The version the editor was loaded with: a template saved by someone else meanwhile answers 409.
        change_time: template?.change_time ?? null,
      };
      return template ? approvalsAdminApi.updateTemplate(template.id, body) : approvalsAdminApi.createTemplate(body);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: approvalsAdminKeys.templates() });
      void qc.invalidateQueries({ queryKey: ["approvals", "templates"] });
      onClose();
    },
    onError: (err) => {
      // Someone saved this template since it was loaded: keep the input (nothing is overwritten) and
      // refresh the list so that reopening shows the other version.
      if (approvalErrorCode(err) === "template_changed") {
        void qc.invalidateQueries({ queryKey: approvalsAdminKeys.templates() });
      }
    },
  });

  const stepName = (i: number) => definition.steps[i]?.name.trim();
  return (
    <Dialog
      open
      onClose={close}
      size="2xl"
      title={editing ? t("approvals.admin.templates.edit") : t("approvals.admin.templates.new")}
      footer={
        <>
          <Button onClick={close} data-testid="template-cancel">
            {t("common.cancel")}
          </Button>
          <Button
            variant="primary"
            disabled={!canSave || save.isPending}
            onClick={() => {
              setTouched(true);
              save.mutate();
            }}
            data-testid="template-save"
          >
            {t("common.save")}
          </Button>
        </>
      }
    >
      <div className="space-y-4" data-testid="template-dialog">
        {template && template.origin !== "admin" && (
          <p className="rounded-md border border-hairline bg-surface-subtle p-2 text-xs text-muted" data-testid="template-origin-hint">
            {t("approvals.admin.templates.originHint", { feature: template.origin })}
          </p>
        )}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className="block text-xs font-medium text-muted">
            {t("approvals.admin.templates.key")}
            <input
              className={`${inputCls} mt-1`}
              value={key}
              maxLength={100}
              readOnly={editing}
              onChange={(e) => setKey(e.target.value)}
              onBlur={() => setKeyLeft(true)}
              aria-invalid={!editing && (touched || keyLeft) && !keyOk}
              data-testid="template-key"
            />
            {!editing && (touched || keyLeft) && !keyOk && (
              <span className="mt-1 block text-danger" role="alert" data-testid="template-key-error">
                {t("approvals.admin.templates.keyMissing")}
              </span>
            )}
            <span className="mt-1 block text-[11px] font-normal">
              {editing ? t("approvals.admin.templates.keyLocked") : t("approvals.admin.templates.keyHint")}
            </span>
          </label>
          <label className="block text-xs font-medium text-muted">
            {t("approvals.admin.templates.name")}
            <input className={`${inputCls} mt-1`} value={name} maxLength={200} onChange={(e) => setName(e.target.value)} data-testid="template-name" />
          </label>
          <label className="block text-xs font-medium text-muted sm:col-span-2">
            {t("approvals.admin.templates.description")}
            <textarea className={`${inputCls} mt-1`} rows={2} value={description} onChange={(e) => setDescription(e.target.value)} data-testid="template-description" />
          </label>
          <fieldset className="text-xs font-medium text-muted" data-testid="template-subjects">
            <legend>{t("approvals.admin.templates.subjectTypes")}</legend>
            <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-sm font-normal text-ink">
              {[...new Set([...subjects.types.map((s) => s.key), ...subjectTypes])].map((k) => (
                <label key={k} className="flex items-center gap-2">
                  <input type="checkbox" checked={subjectTypes.includes(k)} onChange={() => toggleSubject(k)} data-testid={`template-subject-${k}`} />
                  {subjects.title(k)}
                </label>
              ))}
            </div>
            <span className="mt-1 block text-[11px] font-normal">{t("approvals.admin.templates.subjectTypesHint")}</span>
          </fieldset>
          <label className="flex items-center gap-2 self-end pb-1.5 text-sm text-ink">
            <Switch checked={valid} onChange={setValid} testId="template-valid" aria-label={t("approvals.admin.templates.valid")} />
            {t("approvals.admin.templates.valid")}
          </label>
        </div>

        <div>
          <h3 className="mb-2 text-sm font-semibold text-ink">{t("approvals.admin.templates.definition")}</h3>
          <DefinitionEditor value={definition} onChange={setDefinition} subjectType={subjectTypes[0] ?? null} allowCustomers allowAutoApprove />
        </div>

        <div className="space-y-2" aria-live="polite" data-testid="template-check">
          {!checkCurrent ? (
            <p className="text-xs text-muted" data-testid="template-check-pending">
              {check.isError ? approvalErrorMessage(check.error, t) : t("approvals.admin.templates.check.running")}
            </p>
          ) : errors.length === 0 ? (
            <p className="text-xs text-green" data-testid="template-check-ok">
              {t("approvals.admin.templates.check.ok")}
            </p>
          ) : (
            <ul className="space-y-1 text-xs text-danger" role="alert" data-testid="template-errors">
              {errors.map((e, i) => (
                <li key={i} data-testid="template-error" data-step={e.step ?? undefined}>
                  {e.step !== null
                    ? t("approvals.admin.templates.check.stepPrefix", { n: e.step + 1, name: stepName(e.step) || "–" })
                    : null}
                  {e.step !== null ? " " : ""}
                  {e.text}
                </li>
              ))}
            </ul>
          )}
          {warnings.length > 0 && (
            <ul className="space-y-1 text-xs text-warn" data-testid="template-warnings">
              {warnings.map((w, i) => (
                <li key={i}>{w.text}</li>
              ))}
            </ul>
          )}
        </div>

        {save.isError && (
          <p className="text-sm text-danger" role="alert" data-testid="template-save-error">
            {approvalErrorMessage(save.error, t)}
          </p>
        )}
      </div>
      <ConfirmDialog
        open={discarding}
        title={t("approvals.admin.discardTitle")}
        message={t("approvals.admin.discardMessage")}
        variant="danger"
        confirmLabel={t("approvals.admin.discard")}
        onConfirm={onClose}
        onCancel={() => setDiscarding(false)}
      />
    </Dialog>
  );
}

/** Reusable approval flows: key, name, subject types and steps. Features may own a template (origin). */
export function ApprovalTemplatesPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [editor, setEditor] = useState<{ template: ApprovalTemplate | null } | null>(null);
  const [deleting, setDeleting] = useState<ApprovalTemplate | null>(null);
  const subjects = useSubjectTypes();
  const q = useQuery({
    queryKey: approvalsAdminKeys.templates(),
    queryFn: ({ signal }) => approvalsAdminApi.templates(signal),
  });
  const remove = useMutation({
    mutationFn: (tpl: ApprovalTemplate) => approvalsAdminApi.deleteTemplate(tpl.id),
    onSuccess: () => {
      setDeleting(null);
      void qc.invalidateQueries({ queryKey: approvalsAdminKeys.templates() });
      void qc.invalidateQueries({ queryKey: ["approvals", "templates"] });
    },
  });
  const rows = q.data ?? [];

  return (
    <div className="space-y-4 p-4" data-testid="admin-approval-templates-page">
      <AdminApprovalsHeader
        title={t("admin.nav.approvalsTemplates")}
        action={
          <Button variant="primary" onClick={() => setEditor({ template: null })} data-testid="template-new">
            {t("approvals.admin.templates.new")}
          </Button>
        }
      />
      {q.isError ? (
        <p className="text-sm text-danger" role="alert" data-testid="templates-error">
          {t("approvals.agent.loadFailed")}
        </p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-muted" data-testid="templates-empty">
          {q.isPending ? t("common.loading") : t("approvals.admin.templates.empty")}
        </p>
      ) : (
        <ul className="divide-y divide-hairline rounded-md border border-hairline bg-surface" data-testid="templates-list">
          {rows.map((tpl) => (
            <li key={tpl.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2" data-testid="template-row" data-template-key={tpl.template_key}>
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-ink">
                  {tpl.name} <span className="font-mono text-xs font-normal text-muted">{tpl.template_key}</span>
                </p>
                <p className="flex flex-wrap items-center gap-1 text-xs text-muted">
                  {tpl.subject_types.length === 0
                    ? t("approvals.admin.templates.allSubjects")
                    : tpl.subject_types.map(subjects.title).join(", ")}
                  {tpl.origin !== "admin" && (
                    <Badge tone="muted" data-testid="template-origin">
                      {t("approvals.admin.templates.originHint", { feature: tpl.origin })}
                    </Badge>
                  )}
                  {!tpl.valid && (
                    <Badge tone="warn" data-testid="template-invalid">
                      {t("approvals.admin.templates.invalid")}
                    </Badge>
                  )}
                  {tpl.warnings.length > 0 && (
                    <Badge tone="warn" data-testid="template-has-warnings">
                      {t("approvals.admin.templates.hasWarnings", { count: tpl.warnings.length })}
                    </Badge>
                  )}
                </p>
              </div>
              <div className="flex gap-2">
                <Button size="sm" onClick={() => setEditor({ template: tpl })} data-testid="template-edit">
                  {t("common.edit")}
                </Button>
                <Button size="sm" variant="danger" onClick={() => setDeleting(tpl)} data-testid="template-delete">
                  {t("common.delete")}
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {editor && <TemplateDialog key={editor.template?.id ?? "new"} template={editor.template} onClose={() => setEditor(null)} />}
      <ConfirmDialog
        open={deleting !== null}
        title={t("approvals.admin.templates.deleteTitle")}
        message={
          <>
            <p>{t("approvals.admin.templates.deleteMessage", { name: deleting?.name ?? "" })}</p>
            {remove.isError && (
              <p className="mt-2 text-danger" role="alert" data-testid="template-delete-error">
                {approvalErrorMessage(remove.error, t)}
              </p>
            )}
          </>
        }
        variant="danger"
        confirmLabel={t("common.delete")}
        pending={remove.isPending}
        onConfirm={() => deleting && remove.mutate(deleting)}
        onCancel={() => {
          remove.reset();
          setDeleting(null);
        }}
      />
    </div>
  );
}
