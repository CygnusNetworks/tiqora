import { useEffect, useRef, useState, type FormEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Spinner } from "@/components/ui/Spinner";
import { JobProgress } from "@/components/jobs/JobProgress";
import { useJob } from "@/components/jobs/useJob";
import { apiErrorMessage } from "@/lib/avatarApi";
import { webservicesApi, type DryRunResult } from "@/lib/webservicesApi";
import { prettyBody } from "./prettyBody";

function positiveInt(raw: string): number | null {
  const n = Number(raw.trim());
  return /^\d+$/.test(raw.trim()) && Number.isSafeInteger(n) && n > 0 ? n : null;
}

/**
 * "Probelauf": build the request of one invoker for a ticket exactly as a real
 * call would and show it masked. Nothing is sent. The work is a background job
 * (it may read the ticket and refresh an OAuth2 token), so the dialog follows it.
 */
export function DryRunDialog({
  wsId,
  invoker,
  events,
  onClose,
}: {
  wsId: number;
  invoker: string;
  events: string[];
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [ticketId, setTicketId] = useState("");
  const [articleId, setArticleId] = useState("");
  const [event, setEvent] = useState(events[0] ?? "TicketCreate");
  const [jobId, setJobId] = useState<number | null>(null);
  const { job, isTerminal, error: jobError } = useJob(jobId);

  const ticket = positiveInt(ticketId);
  const article = articleId.trim() === "" ? null : positiveInt(articleId);
  const valid = ticket != null && (articleId.trim() === "" || article != null);

  const start = useMutation({
    mutationFn: () =>
      webservicesApi.dryRun(wsId, invoker, {
        ticket_id: ticket as number,
        article_id: article,
        event,
      }),
    onSuccess: (ref) => setJobId(ref.job_id),
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (valid && !start.isPending) start.mutate();
  };

  const firstField = useRef<HTMLInputElement | null>(null);
  useEffect(() => {
    firstField.current?.focus();
  }, []);

  const result = job?.status === "succeeded" ? (job.result as DryRunResult | null) : null;
  const request = result?.request;

  return (
    <Dialog
      open
      onClose={onClose}
      title={t("admin.webservices.dryRun.title", { invoker })}
      description={t("admin.webservices.dryRun.description")}
      size="2xl"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose} data-testid="dryrun-close">
            {t("jobs.close")}
          </Button>
          {jobId == null && (
            <Button
              variant="primary"
              size="sm"
              type="submit"
              form="dryrun-form"
              disabled={!valid || start.isPending}
              data-testid="dryrun-submit"
            >
              {t("admin.webservices.dryRun.start")}
            </Button>
          )}
        </>
      }
    >
      {jobId == null ? (
        <form id="dryrun-form" onSubmit={submit} className="space-y-3" data-testid="dryrun-form">
          <label className="block text-sm">
            <span className="mb-1 block font-medium text-ink">
              {t("admin.webservices.dryRun.ticketId")}
            </span>
            <input
              ref={firstField}
              inputMode="numeric"
              value={ticketId}
              onChange={(e) => setTicketId(e.target.value)}
              className="w-full rounded-lg border border-hairline bg-surface px-2 py-1.5 text-sm text-ink"
              data-testid="dryrun-ticket"
            />
          </label>
          <label className="block text-sm">
            <span className="mb-1 block font-medium text-ink">
              {t("admin.webservices.dryRun.articleId")}
            </span>
            <input
              inputMode="numeric"
              value={articleId}
              onChange={(e) => setArticleId(e.target.value)}
              className="w-full rounded-lg border border-hairline bg-surface px-2 py-1.5 text-sm text-ink"
              data-testid="dryrun-article"
            />
          </label>
          <label className="block text-sm">
            <span className="mb-1 block font-medium text-ink">
              {t("admin.webservices.dryRun.event")}
            </span>
            <input
              list="dryrun-events"
              value={event}
              onChange={(e) => setEvent(e.target.value)}
              className="w-full rounded-lg border border-hairline bg-surface px-2 py-1.5 font-mono text-sm text-ink"
              data-testid="dryrun-event"
            />
            <datalist id="dryrun-events">
              {events.map((name) => (
                <option key={name} value={name} />
              ))}
            </datalist>
          </label>
          <p className="text-xs text-muted">{t("admin.webservices.dryRun.nothingSent")}</p>
          {start.isError && (
            <p className="text-xs text-danger" role="alert" data-testid="dryrun-error">
              {apiErrorMessage(start.error, t("admin.webservices.dryRun.startFailed"))}
            </p>
          )}
        </form>
      ) : (
        <div className="space-y-4">
          {job ? (
            <JobProgress job={job} testId="dryrun-job" />
          ) : jobError ? (
            <p className="text-sm text-danger" data-testid="dryrun-job-load-error">
              {t("jobs.loadError")}
            </p>
          ) : (
            <Spinner />
          )}
          {isTerminal && request && (
            <div className="space-y-3" data-testid="dryrun-result">
              <div className="flex flex-wrap items-baseline gap-2 text-sm">
                <span className="rounded border border-hairline bg-surface-subtle px-1.5 py-0.5 font-mono text-xs font-semibold text-ink" data-testid="dryrun-method">
                  {request.method}
                </span>
                <span className="break-all font-mono text-xs text-ink" data-testid="dryrun-url">
                  {request.url}
                </span>
              </div>
              <div>
                <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">
                  {t("admin.webservices.dryRun.headers")}
                </h3>
                <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 font-mono text-xs" data-testid="dryrun-headers">
                  {Object.entries(request.headers ?? {}).map(([name, value]) => (
                    <div key={name} className="contents">
                      <dt className="text-muted">{name}</dt>
                      <dd className="break-all text-ink">{value}</dd>
                    </div>
                  ))}
                </dl>
                <p className="mt-1 text-[11px] text-muted">{t("admin.webservices.dryRun.masked")}</p>
              </div>
              <div>
                <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">
                  {t("admin.webservices.dryRun.body")}
                </h3>
                <pre className="max-h-72 overflow-auto rounded-lg border border-hairline bg-surface-subtle p-2 font-mono text-xs text-ink" data-testid="dryrun-body">
                  {prettyBody(request.body ?? "")}
                </pre>
              </div>
            </div>
          )}
        </div>
      )}
    </Dialog>
  );
}
