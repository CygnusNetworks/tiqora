import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  api,
  type SubjectConfigOut,
  type SubjectConfigUpdate,
  type SubjectFormat,
} from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { HelpPopover } from "@/components/ui/HelpPopover";

const QUERY_KEY = ["admin", "subject-config"] as const;
const PREVIEW_TN = "2026070100000019";
const PREVIEW_BASE = "Re: Beispiel";

type FormState = {
  enabled: boolean;
  hook: string;
  divider: string;
  subject_format: SubjectFormat;
};

function toForm(row: SubjectConfigOut): FormState {
  const fmt = row.subject_format;
  const subject_format: SubjectFormat =
    fmt === "Right" || fmt === "None" || fmt === "Left" ? fmt : "Left";
  return {
    enabled: row.enabled,
    hook: row.hook,
    divider: row.divider,
    subject_format,
  };
}

const emptyForm: FormState = {
  enabled: true,
  hook: "Ticket#",
  divider: "",
  subject_format: "Left",
};

function buildPreview(form: FormState): string {
  if (!form.enabled || form.subject_format === "None") {
    return PREVIEW_BASE;
  }
  const tag = `[${form.hook}${form.divider}${PREVIEW_TN}]`;
  if (form.subject_format === "Right") {
    return `${PREVIEW_BASE} ${tag}`;
  }
  return `${tag} ${PREVIEW_BASE}`;
}

export function SubjectConfigPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [form, setForm] = useState<FormState>(emptyForm);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);

  const settingsQ = useQuery({
    queryKey: QUERY_KEY,
    queryFn: ({ signal }) => api.getSubjectConfig(signal),
  });

  useEffect(() => {
    if (settingsQ.data) {
      setForm(toForm(settingsQ.data));
    }
  }, [settingsQ.data]);

  const saveM = useMutation({
    mutationFn: (body: SubjectConfigUpdate) => api.putSubjectConfig(body),
    onSuccess: (data) => {
      qc.setQueryData(QUERY_KEY, data);
      setForm(toForm(data));
      setStatusMsg(t("admin.subjectConfig.saved"));
    },
    onError: () => setStatusMsg(t("admin.subjectConfig.saveError")),
  });

  const preview = useMemo(() => buildPreview(form), [form]);
  const znuny = settingsQ.data?.znuny;

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    setStatusMsg(null);
    // Always send all four fields so the form is the source of truth for
    // effective overrides. Empty hook clears the override (re-inherit Znuny).
    const body: SubjectConfigUpdate = {
      enabled: form.enabled,
      hook: form.hook.trim() || null,
      divider: form.divider,
      subject_format: form.subject_format,
    };
    saveM.mutate(body);
  };

  const clearOverrides = () => {
    setStatusMsg(null);
    saveM.mutate({
      enabled: null,
      hook: null,
      divider: null,
      subject_format: null,
    });
  };

  if (settingsQ.isLoading) {
    return (
      <div className="flex items-center gap-2 p-4" data-testid="admin-subject-config-page">
        <Spinner />
      </div>
    );
  }

  if (settingsQ.isError) {
    return (
      <div className="p-4 text-sm text-danger" data-testid="admin-subject-config-page">
        {t("admin.subjectConfig.loadError")}
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-2xl space-y-4 p-4" data-testid="admin-subject-config-page">
      <div>
        <h1 className="font-display text-xl font-semibold text-ink">
          {t("admin.subjectConfig.title")}
        </h1>
        <p className="mt-1 text-sm text-muted">{t("admin.subjectConfig.description")}</p>
      </div>

      <form
        onSubmit={onSubmit}
        className="space-y-4 rounded-lg border border-hairline bg-surface p-4"
      >
        <label className="flex items-center gap-2 text-sm text-ink">
          <input
            type="checkbox"
            data-testid="subject-config-enabled"
            checked={form.enabled}
            onChange={(e) => setForm((f) => ({ ...f, enabled: e.target.checked }))}
            className="rounded border-hairline"
          />
          {t("admin.subjectConfig.enabled")}
        </label>

        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block text-sm sm:col-span-2">
            <span className="mb-1 flex items-center gap-1.5 text-muted">
              {t("admin.subjectConfig.hook")}
              <HelpPopover title={t("admin.subjectConfig.hook")} testId="subject-config-help-hook">
                {t("admin.help.subjectConfig.hook")}
              </HelpPopover>
            </span>
            <input
              data-testid="subject-config-hook"
              type="text"
              value={form.hook}
              onChange={(e) => setForm((f) => ({ ...f, hook: e.target.value }))}
              className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 font-mono text-sm text-ink"
              autoComplete="off"
              placeholder={znuny?.hook || "Ticket#"}
            />
            {znuny ? (
              <span className="mt-1 block text-xs text-muted">
                {t("admin.subjectConfig.inheritedFromZnuny", { value: znuny.hook })}
              </span>
            ) : null}
          </label>

          <label className="block text-sm">
            <span className="mb-1 flex items-center gap-1.5 text-muted">
              {t("admin.subjectConfig.divider")}
              <HelpPopover title={t("admin.subjectConfig.divider")} testId="subject-config-help-divider">
                {t("admin.help.subjectConfig.divider")}
              </HelpPopover>
            </span>
            <input
              data-testid="subject-config-divider"
              type="text"
              value={form.divider}
              onChange={(e) => setForm((f) => ({ ...f, divider: e.target.value }))}
              className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 font-mono text-sm text-ink"
              autoComplete="off"
              placeholder={znuny?.divider ?? ""}
            />
            {znuny ? (
              <span className="mt-1 block text-xs text-muted">
                {t("admin.subjectConfig.inheritedFromZnuny", {
                  value: znuny.divider === "" ? t("admin.subjectConfig.empty") : znuny.divider,
                })}
              </span>
            ) : null}
          </label>

          <label className="block text-sm">
            <span className="mb-1 flex items-center gap-1.5 text-muted">
              {t("admin.subjectConfig.subjectFormat")}
              <HelpPopover title={t("admin.subjectConfig.subjectFormat")} testId="subject-config-help-format">
                {t("admin.help.subjectConfig.subjectFormat")}
              </HelpPopover>
            </span>
            <select
              data-testid="subject-config-format"
              value={form.subject_format}
              onChange={(e) =>
                setForm((f) => ({
                  ...f,
                  subject_format: e.target.value as SubjectFormat,
                }))
              }
              className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink"
            >
              <option value="Left">{t("admin.subjectConfig.formatLeft")}</option>
              <option value="Right">{t("admin.subjectConfig.formatRight")}</option>
              <option value="None">{t("admin.subjectConfig.formatNone")}</option>
            </select>
            {znuny ? (
              <span className="mt-1 block text-xs text-muted">
                {t("admin.subjectConfig.inheritedFromZnuny", { value: znuny.subject_format })}
              </span>
            ) : null}
          </label>
        </div>

        <div
          className="rounded-md border border-hairline bg-surface-subtle px-3 py-2"
          data-testid="subject-config-preview"
        >
          <span className="mb-1 block text-xs font-medium uppercase tracking-wide text-muted">
            {t("admin.subjectConfig.preview")}
          </span>
          <code className="break-all text-sm text-ink">{preview}</code>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" disabled={saveM.isPending} data-testid="subject-config-save">
            {saveM.isPending ? t("admin.subjectConfig.saving") : t("admin.subjectConfig.save")}
          </Button>
          <Button
            type="button"
            variant="secondary"
            disabled={saveM.isPending}
            onClick={clearOverrides}
            data-testid="subject-config-reset"
          >
            {t("admin.subjectConfig.resetToZnuny")}
          </Button>
          {statusMsg ? (
            <span className="text-sm text-muted" data-testid="subject-config-status">
              {statusMsg}
            </span>
          ) : null}
        </div>
      </form>
    </div>
  );
}
