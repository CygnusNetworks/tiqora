import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ComposerBody } from "@/components/agent/ComposerBody";
import { JobProgress } from "@/components/jobs/JobProgress";
import { useJob } from "@/components/jobs/useJob";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Switch } from "@/components/ui/Switch";
import { ApiError } from "@/lib/api";
import { itsmApi } from "@/lib/itsmApi";

export type LinkedTarget = { id: number; tn: string; title: string };

type SkippedEntry = { ticket_id: number; reason: string };

const inputCls =
  "mt-1 w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink focus:border-accent";

function JobResult({ result, targets }: { result: Record<string, unknown> | null; targets: LinkedTarget[] }) {
  const { t } = useTranslation();
  const name = (id: number) => targets.find((x) => x.id === id)?.tn ?? String(id);
  const done = Array.isArray(result?.done) ? (result.done as number[]) : [];
  const skipped = Array.isArray(result?.skipped) ? (result.skipped as SkippedEntry[]) : [];
  return (
    <div className="mt-3 space-y-2 text-sm" data-testid="linked-note-result">
      <div>
        <p className="font-medium text-ink">{t("itsm.linkedNote.done", { count: done.length })}</p>
        <ul className="text-xs text-muted" data-testid="linked-note-done">
          {done.map((id) => (
            <li key={id}>{name(id)}</li>
          ))}
        </ul>
      </div>
      {skipped.length > 0 && (
        <div>
          <p className="font-medium text-ink">{t("itsm.linkedNote.skipped", { count: skipped.length })}</p>
          <ul className="text-xs text-muted" data-testid="linked-note-skipped">
            {skipped.map((s) => (
              <li key={s.ticket_id}>
                {name(s.ticket_id)}:{" "}
                {t(`itsm.linkedNote.reasons.${s.reason}`, { defaultValue: t("itsm.linkedNote.reasons.error") })}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/**
 * "Notiz an verknüpfte Tickets": all linked tickets preselected, subject, text, customer
 * visibility. Submitting queues the bulk job; the dialog then follows it and lists the result.
 */
export function LinkedNoteDialog({
  ticketId,
  targets,
  onClose,
}: {
  ticketId: number;
  targets: LinkedTarget[];
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [selected, setSelected] = useState<Set<number>>(() => new Set(targets.map((x) => x.id)));
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [visible, setVisible] = useState(false);
  const [jobId, setJobId] = useState<number | null>(null);
  const { job, isTerminal, cancel, isCancelling } = useJob(jobId);

  const submit = useMutation({
    mutationFn: () =>
      itsmApi.linkedArticles(ticketId, {
        target_ticket_ids: targets.filter((x) => selected.has(x.id)).map((x) => x.id),
        mode: "note",
        subject: subject.trim(),
        body_html: body,
        visible_for_customer: visible,
      }),
    onSuccess: (accepted) => setJobId(accepted.job_id),
  });

  const hasText = body.replace(/<[^>]*>/g, "").trim().length > 0;
  const canSubmit = selected.size > 0 && subject.trim() !== "" && hasText && !submit.isPending;
  const toggle = (id: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const close = () => {
    if (jobId != null) void qc.invalidateQueries({ queryKey: ["tickets"] });
    onClose();
  };

  return (
    <Dialog open onClose={close} title={t("itsm.linkedNote.title")}>
      <div className="space-y-3" data-testid="linked-note-dialog">
        {jobId == null ? (
          <>
            <fieldset className="space-y-1">
              <legend className="text-xs text-muted">{t("itsm.linkedNote.targets")}</legend>
              {targets.map((x) => (
                <label key={x.id} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={selected.has(x.id)}
                    onChange={() => toggle(x.id)}
                    data-testid={`linked-note-target-${x.id}`}
                  />
                  <span className="font-mono text-accent">{x.tn}</span>
                  <span className="truncate text-muted">{x.title}</span>
                </label>
              ))}
            </fieldset>
            <label className="block text-xs text-muted">
              {t("itsm.linkedNote.subject")}
              <input
                className={inputCls}
                value={subject}
                maxLength={250}
                onChange={(e) => setSubject(e.target.value)}
                data-testid="linked-note-subject"
              />
            </label>
            <div className="text-xs text-muted">
              {t("itsm.linkedNote.text")}
              <div className="mt-1 overflow-hidden rounded-lg border border-hairline bg-surface-subtle/40 focus-within:border-accent/60">
                <ComposerBody richText value={body} onChange={setBody} testId="linked-note-body" bare />
              </div>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <Switch checked={visible} onChange={setVisible} testId="linked-note-visible" aria-label={t("itsm.linkedNote.visible")} />
              {t("itsm.linkedNote.visible")}
            </label>
            {submit.isError && (
              <p className="text-xs text-danger" role="alert" data-testid="linked-note-error">
                {submit.error instanceof ApiError && submit.error.status === 422
                  ? t("itsm.linkedNote.invalid")
                  : t("itsm.linkedNote.error")}
              </p>
            )}
            <div className="flex justify-end gap-2">
              <Button variant="secondary" size="sm" onClick={close}>
                {t("common.cancel")}
              </Button>
              <Button
                variant="primary"
                size="sm"
                disabled={!canSubmit}
                onClick={() => submit.mutate()}
                data-testid="linked-note-submit"
              >
                {t("itsm.linkedNote.send")}
              </Button>
            </div>
          </>
        ) : (
          <>
            {job ? <JobProgress job={job} testId="linked-note-job" /> : null}
            {job && isTerminal && <JobResult result={job.result} targets={targets} />}
            <div className="flex justify-end gap-2">
              {job && !isTerminal && (
                <Button variant="secondary" size="sm" onClick={cancel} disabled={isCancelling || job.cancel_requested}>
                  {t("jobs.cancel")}
                </Button>
              )}
              <Button variant="secondary" size="sm" onClick={close} data-testid="linked-note-close">
                {t("jobs.close")}
              </Button>
            </div>
          </>
        )}
      </div>
    </Dialog>
  );
}
