import { useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";
import { inputCls } from "@/components/reports/builder/builderUi";
import { RecipientsFields } from "@/components/reports/schedules/RecipientsFields";
import { RecurrenceFields } from "@/components/reports/schedules/RecurrenceFields";
import { ScheduleReportsPicker } from "@/components/reports/schedules/ScheduleReportsPicker";
import { ScheduleRunsTable } from "@/components/reports/schedules/ScheduleRunsTable";
import {
  defaultSubjectHint,
  emptyForm,
  fromSchedule,
  recurrenceErrors,
  toRequest,
  toUpdateRequest,
  validateForm,
  type ScheduleFormState,
} from "@/components/reports/schedules/scheduleForm";
import { SchedulesGate } from "@/components/reports/schedules/SchedulesGate";
import { Button } from "@/components/ui/Button";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { Spinner } from "@/components/ui/Spinner";
import { Switch } from "@/components/ui/Switch";
import { toBcp47 } from "@/i18n/locales";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDateTime } from "@/lib/format";
import { reportErrorMessage, reportsApi } from "@/lib/reportsApi";
import { reportSchedulesApi, type ReportSchedule } from "@/lib/reportSchedulesApi";
import { displayTimeZone } from "@/lib/timeZone";
import type { ReportScheduleNewSearch } from "./reportScheduleSearch";

function Card({ title, id, children }: { title: string; id: string; children: ReactNode }) {
  return (
    <section aria-labelledby={id} className="space-y-3 rounded-lg border border-hairline bg-surface p-4">
      <h2 id={id} className="text-sm font-semibold text-ink">
        {title}
      </h2>
      {children}
    </section>
  );
}

function Field({ label, htmlFor, error, children }: { label: string; htmlFor: string; error?: string; children: ReactNode }) {
  const { t } = useTranslation();
  return (
    <div>
      <label htmlFor={htmlFor} className="block text-xs font-medium text-muted">
        {label}
      </label>
      <div className="mt-1">{children}</div>
      {error && (
        <p className="mt-1 text-xs text-danger" role="alert">
          {t(error)}
        </p>
      )}
    </div>
  );
}

/** The form of one schedule (new or loaded); remounted with `key` after a reload. */
function ScheduleEditor({
  schedule,
  initial,
  onReload,
}: {
  schedule: ReportSchedule | null;
  initial: ScheduleFormState;
  onReload: () => void;
}) {
  const { t, i18n } = useTranslation();
  const bcp = toBcp47(i18n.resolvedLanguage ?? i18n.language ?? "de");
  const { user } = useAuth();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { confirm, dialog } = useConfirm();
  const [form, setForm] = useState<ScheduleFormState>(initial);
  const [shown, setShown] = useState(false); // validation errors appear after the first save attempt
  const [message, setMessage] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [conflict, setConflict] = useState(false);

  const isOwner = schedule ? schedule.is_owner : true;
  // The server evaluates the rights of the owner; an admin editing someone else's schedule leaves the call to it.
  const canShare = isOwner ? user?.can_share_reports === true : true;
  const externalAllowed = isOwner ? user?.report_external_recipients === true : true;
  const errors = validateForm(form, { canShare, externalAllowed });
  const canEdit = schedule ? schedule.can_edit : true;

  const catalogueQ = useQuery({
    queryKey: ["reports", "catalogue"],
    queryFn: ({ signal }) => reportsApi.catalogue(signal),
    staleTime: 60_000,
  });

  const set = (patch: Partial<ScheduleFormState>) => setForm((f) => ({ ...f, ...patch }));

  const save = useMutation({
    mutationFn: (f: ScheduleFormState) =>
      schedule ? reportSchedulesApi.update(schedule.id, toUpdateRequest(f)) : reportSchedulesApi.create(toRequest(f)),
    onSuccess: (saved) => {
      void qc.invalidateQueries({ queryKey: ["report-schedules"] });
      if (!schedule) {
        void navigate({ to: "/agent/reports/schedules/$scheduleId", params: { scheduleId: String(saved.id) } });
        return;
      }
      qc.setQueryData(["report-schedules", "get", saved.id], saved);
      setForm(fromSchedule(saved));
      setConflict(false);
      setMessage({ tone: "ok", text: t("reports.schedules.saved") });
    },
    onError: (err) => {
      if (err instanceof ApiError && err.status === 409) {
        setConflict(true);
        setMessage(null);
        return;
      }
      setMessage({ tone: "error", text: reportErrorMessage(err, t("reports.schedules.saveFailed")) });
    },
  });

  const remove = useMutation({
    mutationFn: () => reportSchedulesApi.remove(schedule!.id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["report-schedules"] });
      void navigate({ to: "/agent/reports/schedules" });
    },
    onError: (err) => setMessage({ tone: "error", text: reportErrorMessage(err, t("reports.schedules.saveFailed")) }),
  });

  const submit = (f: ScheduleFormState) => {
    setShown(true);
    setMessage(null);
    if (Object.keys(validateForm(f, { canShare, externalAllowed })).length > 0) return;
    save.mutate(f);
  };

  const onDelete = async () => {
    if (!schedule) return;
    const ok = await confirm({
      title: t("reports.schedules.delete"),
      message: t("reports.schedules.deleteConfirm", { name: schedule.name }),
      confirmLabel: t("reports.schedules.delete"),
      variant: "danger",
    });
    if (ok) remove.mutate();
  };

  const shownErrors = shown ? errors : {};
  const recurrenceErr = shown ? recurrenceErrors(form.recurrence) : {};
  const catalogue = catalogueQ.data?.reports ?? [];

  return (
    <form
      className="space-y-4"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        submit(form);
      }}
      data-testid="schedule-editor"
    >
      {schedule?.paused_reason && !schedule.active && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-escalation/40 bg-escalation/10 px-4 py-3" role="status" data-testid="schedule-paused">
          <p className="text-sm text-ink">{t("reports.schedules.pausedReason", { reason: schedule.paused_reason })}</p>
          {canEdit && (
            <Button size="sm" onClick={() => submit({ ...form, active: true })} disabled={save.isPending} data-testid="schedule-resume">
              {t("reports.schedules.resume")}
            </Button>
          )}
        </div>
      )}

      {conflict && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-danger/40 bg-danger/10 px-4 py-3" role="alert" data-testid="schedule-conflict">
          <p className="text-sm text-ink">{t("reports.schedules.staleVersion")}</p>
          <Button size="sm" onClick={onReload} data-testid="schedule-reload">
            {t("reports.schedules.reload")}
          </Button>
        </div>
      )}

      <Card title={t("reports.schedules.general")} id="sch-general">
        <Field label={t("reports.schedules.name")} htmlFor="schedule-name" error={shownErrors.name}>
          <input
            id="schedule-name"
            className={cn(inputCls, "w-full max-w-xl")}
            value={form.name}
            maxLength={200}
            onChange={(e) => set({ name: e.target.value })}
            aria-invalid={shownErrors.name ? true : undefined}
            disabled={!canEdit}
            data-testid="schedule-name"
          />
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <span id="sch-format" className="block text-xs font-medium text-muted">
              {t("reports.schedules.format")}
            </span>
            <div className="mt-1">
              <SegmentedControl
                items={[
                  { value: "pdf", label: "PDF" },
                  { value: "xlsx", label: "Excel" },
                  { value: "csv", label: "CSV" },
                ]}
                value={form.format}
                onChange={(format) => set({ format })}
                aria-labelledby="sch-format"
                disabled={!canEdit}
                testId="schedule-format"
              />
            </div>
          </div>
          <div>
            <span id="sch-language" className="block text-xs font-medium text-muted">
              {t("reports.schedules.language")}
            </span>
            <div className="mt-1">
              <SegmentedControl
                items={[
                  { value: "de", label: "Deutsch" },
                  { value: "en", label: "English" },
                ]}
                value={form.language}
                onChange={(language) => set({ language })}
                aria-labelledby="sch-language"
                disabled={!canEdit}
                testId="schedule-language"
              />
            </div>
          </div>
        </div>
        <label className="flex items-center gap-2 text-sm text-ink">
          <Switch checked={form.onlyIfNonempty} onChange={(onlyIfNonempty) => set({ onlyIfNonempty })} disabled={!canEdit} testId="schedule-only-if-nonempty" aria-label={t("reports.schedules.onlyIfNonempty")} />
          {t("reports.schedules.onlyIfNonempty")}
        </label>
        <label className="flex items-center gap-2 text-sm text-ink">
          <Switch checked={form.active} onChange={(active) => set({ active })} disabled={!canEdit} testId="schedule-active" aria-label={t("reports.schedules.active")} />
          {t("reports.schedules.active")}
        </label>
      </Card>

      <Card title={t("reports.schedules.items")} id="sch-items">
        <ScheduleReportsPicker
          value={form.items}
          onChange={(items) => set({ items })}
          catalogue={catalogue}
          views={schedule?.items}
          error={shownErrors.items}
        />
      </Card>

      <Card title={t("reports.schedules.recurrence")} id="sch-recurrence">
        <RecurrenceFields
          value={form.recurrence}
          onChange={(recurrence) => set({ recurrence })}
          timeZone={form.timeZone}
          onTimeZoneChange={(timeZone) => set({ timeZone })}
          language={form.language}
          errors={recurrenceErr}
        />
      </Card>

      <Card title={t("reports.schedules.recipients")} id="sch-recipients">
        <RecipientsFields
          value={form.recipients}
          onChange={(recipients) => set({ recipients })}
          ownerId={form.ownerId}
          canShare={canShare}
          externalAllowed={externalAllowed}
          views={schedule?.recipients}
          error={shownErrors.recipients}
        />
      </Card>

      <Card title={t("reports.schedules.message")} id="sch-message">
        <Field label={t("reports.schedules.subject")} htmlFor="schedule-subject">
          <input
            id="schedule-subject"
            className={cn(inputCls, "w-full max-w-xl")}
            value={form.subject}
            maxLength={250}
            placeholder={defaultSubjectHint(form.language)}
            onChange={(e) => set({ subject: e.target.value })}
            disabled={!canEdit}
            data-testid="schedule-subject"
          />
        </Field>
        <Field label={t("reports.schedules.body")} htmlFor="schedule-body">
          <textarea
            id="schedule-body"
            className={cn(inputCls, "min-h-24 w-full max-w-xl")}
            value={form.body}
            maxLength={10000}
            onChange={(e) => set({ body: e.target.value })}
            disabled={!canEdit}
            data-testid="schedule-body"
          />
        </Field>
      </Card>

      {message && (
        <p className={message.tone === "ok" ? "text-sm text-ink" : "text-sm text-danger"} role={message.tone === "ok" ? "status" : "alert"} data-testid="schedule-message">
          {message.text}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {canEdit && (
          <Button type="submit" variant="primary" disabled={save.isPending} data-testid="schedule-save">
            {t("reports.schedules.save")}
          </Button>
        )}
        <Link
          to="/agent/reports/schedules"
          className="inline-flex items-center rounded-md border border-hairline bg-surface px-3 py-1.5 text-sm font-medium text-ink transition-colors hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          {t("common.cancel")}
        </Link>
        {schedule && canEdit && (
          <Button variant="danger" onClick={() => void onDelete()} disabled={remove.isPending} className="ml-auto" data-testid="schedule-delete">
            {t("reports.schedules.delete")}
          </Button>
        )}
      </div>

      {schedule && (
        <p className="text-xs text-muted" data-testid="schedule-next-run">
          {t("reports.schedules.nextRun")}: {formatDateTime(schedule.next_run_at, bcp)}
        </p>
      )}
      {dialog}
    </form>
  );
}

function EditorContent() {
  const { t, i18n } = useTranslation();
  const { user } = useAuth();
  const params = useParams({ strict: false }) as { scheduleId?: string };
  const search = useSearch({ strict: false }) as ReportScheduleNewSearch;
  const id = params.scheduleId != null ? Number(params.scheduleId) : null;
  // "/schedules/abc" never loads: say not found instead of waiting for a disabled query
  const badId = id != null && !(Number.isSafeInteger(id) && id > 0);
  const qc = useQueryClient();
  const [reloads, setReloads] = useState(0);

  const scheduleQ = useQuery({
    queryKey: ["report-schedules", "get", id],
    queryFn: ({ signal }) => reportSchedulesApi.get(id!, signal),
    enabled: id != null && !badId,
    retry: false,
  });

  const back = (
    <Link to="/agent/reports/schedules" className="inline-flex items-center gap-1 text-xs text-muted hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent">
      <span aria-hidden>←</span> {t("reports.schedules.title")}
    </Link>
  );

  if (id != null && !badId && scheduleQ.isPending) return <Spinner />;
  if (id != null && (badId || scheduleQ.isError || !scheduleQ.data)) {
    const gone = badId || (scheduleQ.error instanceof ApiError && scheduleQ.error.status === 404);
    return (
      <div className="space-y-3">
        {back}
        <p className="rounded-lg border border-hairline bg-surface px-4 py-6 text-sm text-ink" role={gone ? "status" : "alert"} data-testid="schedule-missing">
          {gone ? t("reports.schedules.notFound") : reportErrorMessage(scheduleQ.error, t("reports.schedules.loadFailed"))}
        </p>
      </div>
    );
  }

  const schedule = id != null ? scheduleQ.data! : null;
  if (!schedule && !user) return <Spinner />; // the gate renders this page for a signed-in agent only
  const initial = schedule
    ? fromSchedule(schedule)
    : emptyForm({
        ownerId: user!.id,
        reportKey: search.report ?? null,
        timeZone: displayTimeZone(),
        language: i18n.language.startsWith("en") ? "en" : "de",
      });

  return (
    <div className="space-y-5">
      {back}
      <h1 className="font-display text-2xl font-semibold tracking-tight text-ink" data-testid="schedule-title">
        {schedule ? schedule.name : t("reports.schedules.create")}
      </h1>
      {/* No version in the key: after a save the refetched version must not remount the form (and drop the notice); a conflict reload remounts via `reloads`. */}
      <ScheduleEditor
        key={`${schedule?.id ?? "new"}-${reloads}`}
        schedule={schedule}
        initial={initial}
        onReload={() => {
          void qc.invalidateQueries({ queryKey: ["report-schedules", "get", id] }).then(() => setReloads((n) => n + 1));
        }}
      />
      {schedule && <ScheduleRunsTable scheduleId={schedule.id} canRun={schedule.can_edit} isOwner={schedule.is_owner} />}
    </div>
  );
}

/** `/agent/reports/schedules/new` and `/agent/reports/schedules/$scheduleId`. */
export function ReportScheduleEditorPage() {
  return (
    <SchedulesGate>
      <div className="mx-auto w-full max-w-4xl px-4 py-6" data-testid="report-schedule-editor-page">
        <EditorContent />
      </div>
    </SchedulesGate>
  );
}
