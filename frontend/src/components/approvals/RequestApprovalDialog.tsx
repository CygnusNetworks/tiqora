import { useEffect, useId, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { SelectField } from "@/components/ui/SelectField";
import {
  approvalErrorMessage,
  approvalsApi,
  approvalsKeys,
  type ApprovalDetail,
  type ApprovalRequestIn,
} from "@/lib/approvalsApi";
import { DefinitionEditor } from "./DefinitionEditor";
import { definitionComplete, type ApprovalDefinition } from "./definitionLogic";
import { inputCls } from "./pickers";

type Mode = "template" | "custom";

const blankDefinition = (): ApprovalDefinition => ({ steps: [] });

/** "Freigabe anfordern" on a ticket: a template, or own approvers (never "approve on timeout", F9). */
export function RequestApprovalDialog({
  open,
  onClose,
  ticketId,
  ticketTitle,
  onDone,
}: {
  open: boolean;
  onClose: () => void;
  ticketId: number;
  ticketTitle: string;
  onDone: (detail: ApprovalDetail) => void;
}) {
  const { t } = useTranslation();
  const ids = useId();
  const [mode, setMode] = useState<Mode>("template");
  const [templateKey, setTemplateKey] = useState<string | null>(null);
  const [title, setTitle] = useState(ticketTitle);
  const [description, setDescription] = useState("");
  const [definition, setDefinition] = useState<ApprovalDefinition>(blankDefinition);
  const [touched, setTouched] = useState(false);

  const templates = useQuery({
    queryKey: approvalsKeys.templates("ticket"),
    queryFn: ({ signal }) => approvalsApi.templates("ticket", signal),
    enabled: open,
    staleTime: 60_000,
  });
  const mutation = useMutation({
    mutationFn: (body: ApprovalRequestIn) => approvalsApi.requestApproval(ticketId, body),
    onSuccess: (detail) => onDone(detail),
  });
  const reset = mutation.reset;
  useEffect(() => {
    if (!open) return;
    setMode("template");
    setTemplateKey(null);
    setTitle(ticketTitle);
    setDescription("");
    setDefinition(blankDefinition());
    setTouched(false);
    reset();
  }, [open, reset, ticketTitle]);

  const options = templates.data ?? [];
  const noTemplates = templates.isSuccess && options.length === 0;
  const titleMissing = title.trim() === "";
  const problem =
    mode === "template"
      ? templateKey === null
        ? "templateMissing"
        : null
      : definitionComplete(definition)
        ? null
        : "definitionIncomplete";
  const submit = () => {
    setTouched(true);
    if (titleMissing || problem !== null || mutation.isPending) return;
    const base = { title: title.trim(), description: description.trim() || null, approval_key: "default" };
    mutation.mutate(mode === "template" ? { ...base, template_key: templateKey } : { ...base, definition });
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t("approvals.request.title")}
      size="lg"
      footer={
        <>
          <Button onClick={onClose} data-testid="request-cancel">
            {t("common.cancel")}
          </Button>
          <Button variant="primary" onClick={submit} disabled={mutation.isPending} data-testid="request-submit">
            {t("approvals.request.submit")}
          </Button>
        </>
      }
    >
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <SegmentedControl<Mode>
          items={[
            { value: "template", label: t("approvals.request.modeTemplate") },
            { value: "custom", label: t("approvals.request.modeCustom") },
          ]}
          value={mode}
          onChange={setMode}
          testId="request-mode"
          aria-label={t("approvals.request.mode")}
          fullWidth
        />
        <div>
          <label htmlFor={`${ids}-title`} className="mb-1 block text-xs font-medium text-muted">
            {t("approvals.request.name")}
          </label>
          <input
            id={`${ids}-title`}
            className={inputCls}
            value={title}
            maxLength={250}
            onChange={(e) => setTitle(e.target.value)}
            aria-invalid={touched && titleMissing}
            data-testid="request-title"
          />
          {touched && titleMissing && (
            <p className="mt-1 text-xs text-danger" role="alert" data-testid="request-title-error">
              {t("approvals.request.nameMissing")}
            </p>
          )}
        </div>
        <div>
          <label htmlFor={`${ids}-desc`} className="mb-1 block text-xs font-medium text-muted">
            {t("approvals.request.description")}
          </label>
          <textarea
            id={`${ids}-desc`}
            className={inputCls}
            rows={3}
            maxLength={20000}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            data-testid="request-description"
          />
        </div>
        {mode === "template" ? (
          <div>
            {noTemplates ? (
              <p className="text-sm text-muted" data-testid="request-no-templates">
                {t("approvals.request.noTemplates")}
              </p>
            ) : (
              <SelectField
                items={options.map((o) => ({ value: o.key, label: o.name, hint: o.description ?? undefined }))}
                value={templateKey}
                onChange={setTemplateKey}
                testId="request-template"
                aria-label={t("approvals.request.template")}
                placeholder={t("approvals.request.templatePlaceholder")}
              />
            )}
            {touched && problem === "templateMissing" && (
              <p className="mt-1 text-xs text-danger" role="alert" data-testid="request-problem">
                {t("approvals.request.templateMissing")}
              </p>
            )}
          </div>
        ) : (
          <div>
            <DefinitionEditor value={definition} onChange={setDefinition} subjectType="ticket" allowCustomers={false} />
            {touched && problem === "definitionIncomplete" && (
              <p className="mt-1 text-xs text-danger" role="alert" data-testid="request-problem">
                {t("approvals.request.definitionIncomplete")}
              </p>
            )}
          </div>
        )}
        {mutation.isError && (
          <p className="text-xs text-danger" role="alert" data-testid="request-error">
            {approvalErrorMessage(mutation.error, t)}
          </p>
        )}
      </form>
    </Dialog>
  );
}
