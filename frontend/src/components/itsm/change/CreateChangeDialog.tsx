import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { flattenQueues } from "@/components/agent/QueueTree";
import { CiPicker } from "@/components/itsm/CiPicker";
import { JobProgress } from "@/components/jobs/JobProgress";
import { useJob } from "@/components/jobs/useJob";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { SelectField } from "@/components/ui/SelectField";
import { Switch } from "@/components/ui/Switch";
import { Spinner } from "@/components/ui/Spinner";
import { api } from "@/lib/api";
import { changeApi, changeErrorCode, changeKeys, type ChangeCreated, type ChangeType } from "@/lib/changeApi";
import { CHANGE_TYPES } from "@/lib/changePhases";
import type { CiSummary } from "@/lib/itsmApi";
import { warningText } from "./changeUi";

const FIELD_CLASS =
  "w-full rounded-lg border border-hairline bg-surface px-3 py-1.5 text-sm text-ink focus-visible:border-accent focus-visible:outline-none";

/** `datetime-local` value (`2026-10-24T20:00`) as the UTC instant the API expects. */
const utc = (local: string): string | null => (local ? `${local}:00Z` : null);

type Picked = { ref: string; label: string };

/**
 * "Change anlegen": type, template (a standard change needs a standard template, the other types
 * take a matching template or none), queue, services, affected CIs, planned window (UTC) and text.
 * Creating answers with the new change and, when the template has tasks, the id of the job that
 * creates them: the dialog follows that job before it hands over to `onCreated`.
 */
export function CreateChangeDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  /** Called with the ticket id of the new change once it can be opened. */
  onCreated: (ticketId: number) => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [type, setType] = useState<ChangeType>("normal");
  const [templateId, setTemplateId] = useState<number | "">("");
  const [title, setTitle] = useState("");
  const [queueId, setQueueId] = useState<number | "">("");
  const [serviceIds, setServiceIds] = useState<number[]>([]);
  const [cis, setCis] = useState<Picked[]>([]);
  const [pickingCi, setPickingCi] = useState(false);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [reason, setReason] = useState("");
  const [body, setBody] = useState("");
  const [portalVisible, setPortalVisible] = useState(false);
  // A template fills the portal switch only while the agent has not touched it.
  const [portalTouched, setPortalTouched] = useState(false);
  /** Service ids of the picked template that the service list does not know any more (dropped, like the backend does). */
  const [droppedRefs, setDroppedRefs] = useState<string[]>([]);
  const [created, setCreated] = useState<ChangeCreated | null>(null);

  const templatesQ = useQuery({ queryKey: changeKeys.templates(), queryFn: ({ signal }) => changeApi.templates(signal), retry: false });
  const queuesQ = useQuery({ queryKey: ["queues"], queryFn: () => api.listQueues() });
  const servicesQ = useQuery({ queryKey: ["reference", "services"], queryFn: () => api.listReferenceServices() });

  const templates = useMemo(
    () => (templatesQ.data ?? []).filter((x) => x.change_type === type && x.is_standard === (type === "standard")),
    [templatesQ.data, type],
  );
  const template = templates.find((x) => x.id === templateId) ?? null;
  const queues = useMemo(() => flattenQueues(queuesQ.data ?? []).filter((q) => q.valid), [queuesQ.data]);
  const services = servicesQ.data ?? [];

  const changeType = (next: ChangeType) => {
    setType(next);
    setTemplateId("");
  };
  const pickTemplate = (id: number | "") => {
    setTemplateId(id);
    setDroppedRefs([]);
    const d = templates.find((x) => x.id === id)?.defaults;
    if (!d) return;
    // Only fill what is still empty: the template never overwrites what was typed.
    if (!title.trim() && d.title) setTitle(d.title);
    if (queueId === "" && d.queue_id) setQueueId(d.queue_id);
    if (serviceIds.length === 0 && d.service_ids?.length) {
      // A retired service in the template would fail the create with `service_not_found`: drop it and say so.
      const known = servicesQ.data ? new Set(servicesQ.data.map((s) => s.id)) : null;
      const ids = known ? d.service_ids.filter((sid) => known.has(sid)) : d.service_ids;
      setServiceIds(ids);
      if (known) setDroppedRefs(d.service_ids.filter((sid) => !known.has(sid)).map((sid) => `service_not_found:${sid}`));
    }
    if (!reason.trim() && d.reason) setReason(d.reason);
    if (!portalTouched) setPortalVisible(d.portal_visible);
  };

  const windowInvalid = Boolean(start && end && end <= start);
  const missingTemplate = type === "standard" && templateId === "";
  const canSubmit = title.trim() !== "" && !windowInvalid && !missingTemplate;

  const create = useMutation({
    mutationFn: async (): Promise<ChangeCreated> => {
      const out = await changeApi.create({
        change_type: type,
        title: title.trim(),
        template_id: templateId === "" ? null : templateId,
        queue_id: queueId === "" ? null : queueId,
        service_id: serviceIds[0] ?? null,
        service_ids: serviceIds.slice(1),
        ci_refs: cis.map((c) => c.ref),
        planned_start: utc(start),
        planned_end: utc(end),
        reason: reason.trim() || null,
        body: body.trim() || undefined,
      });
      // `ChangeCreateIn` has no portal flag (the template's default applies): set it right after
      if (out.change.portal_visible === portalVisible) return out;
      try {
        return { ...out, change: await changeApi.update(out.change.ticket_id, { portal_visible: portalVisible }) };
      } catch {
        return { ...out, warnings: [...(out.warnings ?? []), "portal_visible_failed"] };
      }
    },
    onSuccess: (out) => {
      void qc.invalidateQueries({ queryKey: changeKeys.all() });
      setCreated(out);
    },
  });

  const { job, isTerminal } = useJob(created?.tasks_job_id);
  const failedTasks = Array.isArray(job?.result?.failed) ? (job?.result?.failed as unknown[]).length : 0;
  const warnings = created?.warnings ?? [];
  const settled = created !== null && (created.tasks_job_id == null || isTerminal);
  const clean = settled && warnings.length === 0 && failedTasks === 0 && job?.status !== "failed";
  // Nothing to read on the result view: go straight to the change.
  const handedOver = useRef(false);
  const onCreatedRef = useRef(onCreated);
  onCreatedRef.current = onCreated;
  useEffect(() => {
    if (created && clean && !handedOver.current) {
      handedOver.current = true;
      onCreatedRef.current(created.change.ticket_id);
    }
  }, [created, clean]);

  const code = create.isError ? changeErrorCode(create.error) : null;
  // a 403 here is about creating in the queue; the generic `forbidden` text fits every other write
  const errorKey = code === "forbidden" ? "forbidden_create" : code;
  const errorText = create.isError ? t([`itsm.change.errors.${errorKey}`, "itsm.change.errors.generic"]) : null;
  const linked = useMemo(() => new Set(cis.map((c) => c.ref)), [cis]);
  const addCi = (ci: CiSummary) => {
    setCis((prev) => (prev.some((c) => c.ref === ci.ref) ? prev : [...prev, { ref: ci.ref, label: `${ci.number} ${ci.name}` }]));
    setPickingCi(false);
  };
  const serviceName = (id: number) => services.find((s) => s.id === id)?.name ?? `#${id}`;

  if (created) {
    return (
      <Dialog open onClose={onClose} title={t("itsm.change.create.title")} size="lg">
        <div className="space-y-3 text-sm" data-testid="change-create-result">
          <p className="font-medium text-ink">{t("itsm.change.create.created", { tn: created.change.tn })}</p>
          {created.tasks_job_id != null && (
            <div>
              <p className="mb-1 text-xs text-muted">{t("itsm.change.create.tasksJob")}</p>
              {job ? <JobProgress job={job} testId="change-create-job" /> : <Spinner />}
            </div>
          )}
          {failedTasks > 0 && (
            <p className="text-xs text-danger" role="alert" data-testid="change-create-tasks-failed">
              {t("itsm.change.create.tasksFailed", { count: failedTasks })}
            </p>
          )}
          {warnings.length > 0 && (
            <ul className="space-y-1 text-xs text-muted" data-testid="change-create-warnings">
              {warnings.map((w) => (
                <li key={w}>{warningText(w, t, t("itsm.change.create.warnings.generic"))}</li>
              ))}
            </ul>
          )}
          <div className="flex justify-end">
            <Button variant="primary" size="sm" onClick={() => onCreated(created.change.ticket_id)} data-testid="change-create-open">
              {t("itsm.change.create.open")}
            </Button>
          </div>
        </div>
      </Dialog>
    );
  }

  return (
    <>
      <Dialog
        open
        onClose={onClose}
        title={t("itsm.change.create.title")}
        size="xl"
        footer={
          <>
            <Button size="sm" onClick={onClose}>
              {t("common.cancel")}
            </Button>
            <Button
              variant="primary"
              size="sm"
              disabled={!canSubmit || create.isPending}
              onClick={() => create.mutate()}
              data-testid="change-create-submit"
            >
              {t("itsm.change.create.submit")}
            </Button>
          </>
        }
      >
        <form
          className="space-y-4 text-sm"
          data-testid="change-create-form"
          onSubmit={(e) => {
            e.preventDefault();
            if (canSubmit && !create.isPending) create.mutate();
          }}
        >
          <div>
            <span className="mb-1 block text-xs font-medium text-muted">{t("itsm.change.create.type")}</span>
            <SegmentedControl
              items={CHANGE_TYPES.map((v) => ({ value: v, label: t(`itsm.change.types.${v}`) }))}
              value={type}
              onChange={changeType}
              testId="change-create-type"
              aria-label={t("itsm.change.create.type")}
            />
            <p className="mt-1 text-xs text-muted">{t(`itsm.change.create.typeHint.${type}`)}</p>
          </div>

          <div>
            <span className="mb-1 block text-xs font-medium text-muted">
              {t(type === "standard" ? "itsm.change.create.templateRequired" : "itsm.change.create.template")}
            </span>
            <SelectField
              items={[
                ...(type === "standard" ? [] : [{ value: "" as number | "", label: t("itsm.change.create.noTemplate") }]),
                ...templates.map((x) => ({ value: x.id as number | "", label: x.name })),
              ]}
              value={templateId}
              onChange={pickTemplate}
              placeholder={t("itsm.change.create.pickTemplate")}
              testId="change-create-template"
            />
            {templatesQ.isError && (
              <p className="mt-1 text-xs text-danger" role="alert" data-testid="change-create-templates-error">
                {t([`itsm.change.errors.${changeErrorCode(templatesQ.error)}`, "itsm.change.errors.generic"])}
              </p>
            )}
            {template?.description && <p className="mt-1 text-xs text-muted">{template.description}</p>}
            {type === "standard" && !templatesQ.isLoading && templates.length === 0 && (
              <p className="mt-1 text-xs text-danger" data-testid="change-create-no-standard">
                {t("itsm.change.create.noStandardTemplates")}
              </p>
            )}
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-muted" htmlFor="change-create-title">
              {t("itsm.change.create.titleLabel")}
            </label>
            <input
              id="change-create-title"
              data-testid="change-create-title"
              className={FIELD_CLASS}
              value={title}
              maxLength={250}
              onChange={(e) => setTitle(e.target.value)}
            />
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <span className="mb-1 block text-xs font-medium text-muted">{t("itsm.change.create.queue")}</span>
              <SelectField
                items={[
                  { value: "" as number | "", label: t("itsm.change.create.defaultQueue") },
                  ...queues.map((q) => ({ value: q.id as number | "", label: q.name })),
                ]}
                value={queueId}
                onChange={setQueueId}
                testId="change-create-queue"
              />
            </div>
            <div>
              <span className="mb-1 block text-xs font-medium text-muted">{t("itsm.change.create.services")}</span>
              <SelectField
                items={services.filter((s) => !serviceIds.includes(s.id)).map((s) => ({ value: s.id, label: s.name }))}
                value={null}
                onChange={(id) => setServiceIds((prev) => [...prev, id])}
                placeholder={t("itsm.change.create.addService")}
                testId="change-create-service"
              />
              {serviceIds.length > 0 && (
                <ul className="mt-1.5 flex flex-wrap gap-1" data-testid="change-create-services">
                  {serviceIds.map((id) => (
                    <Chip key={id} label={serviceName(id)} remove={t("itsm.change.create.remove")} onRemove={() => setServiceIds((p) => p.filter((x) => x !== id))} />
                  ))}
                </ul>
              )}
              {droppedRefs.length > 0 && (
                <ul className="mt-1.5 space-y-0.5 text-xs text-muted" data-testid="change-create-template-notes">
                  {droppedRefs.map((w) => (
                    <li key={w}>{warningText(w, t)}</li>
                  ))}
                </ul>
              )}
            </div>
          </div>

          <div>
            <span className="mb-1 block text-xs font-medium text-muted">{t("itsm.change.create.cis")}</span>
            <Button size="sm" onClick={() => setPickingCi(true)} data-testid="change-create-add-ci">
              {t("itsm.change.create.addCi")}
            </Button>
            {cis.length > 0 && (
              <ul className="mt-1.5 flex flex-wrap gap-1" data-testid="change-create-cis">
                {cis.map((c) => (
                  <Chip key={c.ref} label={c.label} remove={t("itsm.change.create.remove")} onRemove={() => setCis((p) => p.filter((x) => x.ref !== c.ref))} />
                ))}
              </ul>
            )}
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label className="mb-1 block text-xs font-medium text-muted" htmlFor="change-create-start">
                {t("itsm.change.create.start")}
              </label>
              <input id="change-create-start" data-testid="change-create-start" type="datetime-local" className={FIELD_CLASS} value={start} onChange={(e) => setStart(e.target.value)} />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-muted" htmlFor="change-create-end">
                {t("itsm.change.create.end")}
              </label>
              <input id="change-create-end" data-testid="change-create-end" type="datetime-local" className={FIELD_CLASS} value={end} onChange={(e) => setEnd(e.target.value)} />
            </div>
            <p className={windowInvalid ? "text-xs text-danger sm:col-span-2" : "text-xs text-muted sm:col-span-2"} data-testid="change-create-window-hint" role={windowInvalid ? "alert" : undefined}>
              {t(windowInvalid ? "itsm.change.errors.invalid_window" : "itsm.change.create.windowHint")}
            </p>
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-muted" htmlFor="change-create-reason">
              {t("itsm.change.create.reason")}
            </label>
            <textarea id="change-create-reason" data-testid="change-create-reason" rows={2} className={FIELD_CLASS} value={reason} onChange={(e) => setReason(e.target.value)} />
          </div>
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <Switch
                id="change-create-portal"
                testId="change-create-portal"
                checked={portalVisible}
                onChange={(v) => {
                  setPortalTouched(true);
                  setPortalVisible(v);
                }}
                aria-describedby="change-create-portal-help"
              />
              <label htmlFor="change-create-portal" className="text-sm text-ink">
                {t("itsm.change.portal.label")}
              </label>
            </div>
            <p id="change-create-portal-help" className="text-xs text-muted">
              {t("itsm.change.portal.help")}
            </p>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted" htmlFor="change-create-body">
              {t("itsm.change.create.body")}
            </label>
            <textarea id="change-create-body" data-testid="change-create-body" rows={3} className={FIELD_CLASS} value={body} onChange={(e) => setBody(e.target.value)} />
          </div>

          {errorText && (
            <p className="text-xs text-danger" role="alert" data-testid="change-create-error" data-code={code ?? undefined}>
              {errorText}
            </p>
          )}
        </form>
      </Dialog>
      {pickingCi && <CiPicker onClose={() => setPickingCi(false)} onPick={addCi} linked={linked} />}
    </>
  );
}

function Chip({ label, remove, onRemove }: { label: string; remove: string; onRemove: () => void }) {
  return (
    <li className="inline-flex items-center gap-1 rounded border border-hairline bg-surface-subtle px-1.5 py-0.5 text-xs text-ink">
      {label}
      <button type="button" aria-label={`${remove}: ${label}`} className="text-muted hover:text-danger" onClick={onRemove}>
        ×
      </button>
    </li>
  );
}
