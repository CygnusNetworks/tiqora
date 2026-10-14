import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { JobProgress } from "@/components/jobs/JobProgress";
import { useJob } from "@/components/jobs/useJob";
import { skippedOf } from "./skipped";
import { SkippedTargets } from "./SkippedTargets";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Spinner } from "@/components/ui/Spinner";
import { api, type TicketDetail } from "@/lib/api";
import { apiErrorMessage } from "@/lib/avatarApi";
import { itsmApi, itsmErrorCode, itsmKeys, type Problem } from "@/lib/itsmApi";
import { ticketPerms } from "@/lib/ticket";
import { RouterAnchor } from "@/components/ui/RouterAnchor";

const KB_STATES = new Set(["draft", "review", "published", "archived"]);
const inputCls =
  "mt-1 w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink focus:border-accent";

type Hit = { ticket_id: number; tn: string; title: string };

/** "Incidents verknüpfen": search tickets by number or title, pick several, link them in one call. */
function IncidentPicker({
  ticketId,
  linked,
  onClose,
  onLinked,
}: {
  ticketId: number;
  linked: ReadonlySet<number>;
  onClose: () => void;
  onLinked: () => void;
}) {
  const { t } = useTranslation();
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const [picked, setPicked] = useState<Map<number, Hit>>(new Map());
  useEffect(() => {
    const timer = setTimeout(() => setQ(text.trim()), 250);
    return () => clearTimeout(timer);
  }, [text]);
  const hitsQ = useQuery({
    queryKey: ["tickets", ticketId, "problem-incident-search", q],
    queryFn: ({ signal }) => api.searchTickets({ q, limit: 10 }, signal),
    enabled: q.length >= 2,
    retry: false,
  });
  const link = useMutation({
    mutationFn: () => itsmApi.problem.addIncidents(ticketId, [...picked.keys()]),
    onSuccess: onLinked,
  });
  const hits = (hitsQ.data ?? []).filter((h) => h.ticket_id !== ticketId);
  const toggle = (h: Hit) =>
    setPicked((prev) => {
      const next = new Map(prev);
      if (next.has(h.ticket_id)) next.delete(h.ticket_id);
      else next.set(h.ticket_id, h);
      return next;
    });
  return (
    <Dialog open onClose={onClose} title={t("itsm.problem.link.title")}>
      <div className="space-y-3" data-testid="problem-incident-picker">
        <label className="block text-xs text-muted">
          {t("itsm.problem.link.search")}
          <input
            className={inputCls}
            value={text}
            onChange={(e) => setText(e.target.value)}
            data-testid="problem-incident-search"
          />
        </label>
        {hitsQ.isFetching && <Spinner />}
        <ul className="max-h-60 divide-y divide-hairline overflow-auto text-sm">
          {hits.map((h) => {
            const already = linked.has(h.ticket_id);
            return (
              <li key={h.ticket_id}>
                <label className="flex items-center gap-2 py-1">
                  <input
                    type="checkbox"
                    disabled={already}
                    checked={picked.has(h.ticket_id)}
                    onChange={() => toggle(h)}
                    data-testid={`problem-incident-hit-${h.ticket_id}`}
                  />
                  <span className="font-mono text-xs text-accent">{h.tn}</span>
                  <span className="min-w-0 flex-1 truncate text-ink">{h.title}</span>
                  {already && <span className="text-xs text-muted">{t("itsm.problem.link.already")}</span>}
                </label>
              </li>
            );
          })}
        </ul>
        {link.isError && (
          <p className="text-xs text-danger" role="alert" data-testid="problem-incident-error">
            {apiErrorMessage(link.error, t("itsm.problem.link.error"))}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button
            variant="primary"
            size="sm"
            disabled={picked.size === 0 || link.isPending}
            onClick={() => link.mutate()}
            data-testid="problem-incident-submit"
          >
            {t("itsm.problem.link.submit", { count: picked.size })}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

/** Confirm with the number of tickets, then follow the background job and list its result. */
function CloseIncidentsDialog({
  ticketId,
  count,
  onClose,
}: {
  ticketId: number;
  count: number;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [jobId, setJobId] = useState<number | null>(null);
  const { job, isTerminal, cancel, isCancelling } = useJob(jobId);
  const start = useMutation({
    mutationFn: () => itsmApi.problem.closeIncidents(ticketId),
    onSuccess: (accepted) => setJobId(accepted.job_id),
  });
  const done = Array.isArray(job?.result?.done) ? (job?.result?.done as number[]) : [];
  const skipped = skippedOf(job?.result);
  const close = () => {
    if (jobId != null) void qc.invalidateQueries({ queryKey: ["tickets"] });
    onClose();
  };
  return (
    <Dialog open onClose={close} title={t("itsm.problem.close.title")}>
      <div className="space-y-3 text-sm" data-testid="problem-close-dialog">
        {jobId == null ? (
          <>
            <p className="text-ink" data-testid="problem-close-confirm">
              {t("itsm.problem.close.confirm", { count })}
            </p>
            {start.isError && (
              <p className="text-xs text-danger" role="alert" data-testid="problem-close-error">
                {apiErrorMessage(start.error, t("itsm.problem.close.error"))}
              </p>
            )}
            <div className="flex justify-end gap-2">
              <Button variant="secondary" size="sm" onClick={close}>
                {t("common.cancel")}
              </Button>
              <Button
                variant="primary"
                size="sm"
                disabled={start.isPending}
                onClick={() => start.mutate()}
                data-testid="problem-close-submit"
              >
                {t("itsm.problem.close.submit")}
              </Button>
            </div>
          </>
        ) : (
          <>
            {job ? <JobProgress job={job} testId="problem-close-job" /> : <Spinner />}
            {job && isTerminal && (
              <div data-testid="problem-close-result">
                <p className="font-medium text-ink">{t("itsm.problem.close.done", { count: done.length })}</p>
                <SkippedTargets skipped={skipped} testId="problem-close-skipped" />
              </div>
            )}
            <div className="flex justify-end gap-2">
              {job && !isTerminal && (
                <Button variant="secondary" size="sm" onClick={cancel} disabled={isCancelling || job.cancel_requested}>
                  {t("jobs.cancel")}
                </Button>
              )}
              <Button variant="secondary" size="sm" onClick={close} data-testid="problem-close-finish">
                {t("jobs.close")}
              </Button>
            </div>
          </>
        )}
      </div>
    </Dialog>
  );
}

function ProblemBody({ ticket, categoryConfigured }: { ticket: TicketDetail; categoryConfigured: boolean }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const ticketId = ticket.id;
  const canWrite = ticketPerms(ticket).rw;
  const [picking, setPicking] = useState(false);
  const [closing, setClosing] = useState(false);
  const [draft, setDraft] = useState<{ rootCause: string; workaround: string } | null>(null);

  const problemQ = useQuery({
    queryKey: itsmKeys.problem(ticketId),
    queryFn: ({ signal }) => itsmApi.problem.get(ticketId, signal),
    retry: false,
  });
  const problem: Problem | undefined = problemQ.data;

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: itsmKeys.problem(ticketId) });
    void qc.invalidateQueries({ queryKey: itsmKeys.ticketItsm(ticketId) });
    void qc.invalidateQueries({ queryKey: ["tickets", ticketId, "links"] });
  };
  const save = useMutation({
    mutationFn: (v: { rootCause: string; workaround: string }) =>
      itsmApi.problem.save(ticketId, { root_cause: v.rootCause || null, workaround: v.workaround || null }),
    onSuccess: () => {
      setDraft(null);
      refresh();
    },
  });
  const known = useMutation({ mutationFn: () => itsmApi.problem.markKnownError(ticketId), onSuccess: refresh });

  if (problemQ.isLoading) return <Spinner />;
  if (!problem) {
    return (
      <p className="text-muted" role="status" data-testid="problem-unavailable">
        {t("itsm.problem.unavailable")}
      </p>
    );
  }
  const rootCause = draft?.rootCause ?? problem.root_cause ?? "";
  const workaround = draft?.workaround ?? problem.workaround ?? "";
  const open = problem.incidents.filter((i) => !i.closed);
  const articleState = problem.known_error_article_state;
  const knownErrorFailure = known.isError
    ? itsmErrorCode(known.error) === "known_error_category_missing"
      ? t("itsm.problem.knownError.noCategory")
      : apiErrorMessage(known.error, t("itsm.problem.knownError.error"))
    : null;

  return (
    <div className="space-y-3">
      <div className="grid gap-3 md:grid-cols-2">
        <label className="block text-xs text-muted">
          {t("itsm.problem.rootCause")}
          <textarea
            className={inputCls}
            rows={4}
            value={rootCause}
            readOnly={!canWrite}
            onChange={(e) => setDraft({ rootCause: e.target.value, workaround })}
            data-testid="problem-root-cause"
          />
        </label>
        <label className="block text-xs text-muted">
          {t("itsm.problem.workaround")}
          <textarea
            className={inputCls}
            rows={4}
            value={workaround}
            readOnly={!canWrite}
            onChange={(e) => setDraft({ rootCause, workaround: e.target.value })}
            data-testid="problem-workaround"
          />
        </label>
      </div>
      {canWrite && draft && (
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="primary"
            disabled={save.isPending}
            onClick={() => save.mutate({ rootCause, workaround })}
            data-testid="problem-save"
          >
            {t("common.save")}
          </Button>
          {save.isError && (
            <span className="text-xs text-danger" role="alert" data-testid="problem-save-error">
              {apiErrorMessage(save.error, t("itsm.problem.saveError"))}
            </span>
          )}
        </div>
      )}

      <div className="space-y-1">
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-xs font-medium uppercase tracking-wide text-muted">{t("itsm.problem.incidents")}</h3>
          {canWrite && (
            <Button size="sm" onClick={() => setPicking(true)} data-testid="problem-link-incidents">
              {t("itsm.problem.link.open")}
            </Button>
          )}
        </div>
        {problem.incidents.length === 0 ? (
          <p className="text-muted" data-testid="problem-incidents-empty">
            {t("itsm.problem.noIncidents")}
          </p>
        ) : (
          <ul className="divide-y divide-hairline" data-testid="problem-incidents">
            {problem.incidents.map((i) => (
              <li key={i.ticket_id} className="flex items-center gap-3 py-1.5" data-testid={`problem-incident-${i.ticket_id}`}>
                <RouterAnchor href={`/agent/tickets/${i.ticket_id}`} className="font-mono text-xs text-accent hover:underline">
                  {i.tn}
                </RouterAnchor>
                <span className="min-w-0 flex-1 truncate text-ink">{i.title}</span>
                <Badge tone={i.closed ? "default" : "accent"}>{i.state}</Badge>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2" data-testid="problem-actions">
        {problem.known_error && <Badge tone="warn" data-testid="problem-known-error-badge">{t("itsm.problem.knownError.badge")}</Badge>}
        {problem.known_error_article_id != null && (
          <span className="text-xs text-muted" data-testid="problem-article">
            <RouterAnchor href={`/agent/kb/${problem.known_error_article_id}`} className="text-accent hover:underline">
              {t("itsm.problem.knownError.article")}
            </RouterAnchor>{" "}
            {articleState && (
              <Badge data-testid="problem-article-state">
                {KB_STATES.has(articleState) ? t(`itsm.problem.knownError.states.${articleState}`) : articleState}
              </Badge>
            )}
          </span>
        )}
        {canWrite && (
          <Button
            size="sm"
            disabled={!categoryConfigured || known.isPending}
            onClick={() => known.mutate()}
            title={categoryConfigured ? undefined : t("itsm.problem.knownError.noCategory")}
            data-testid="problem-known-error"
          >
            {t("itsm.problem.knownError.publish")}
          </Button>
        )}
        {canWrite && open.length > 0 && (
          <Button size="sm" onClick={() => setClosing(true)} data-testid="problem-close-incidents">
            {t("itsm.problem.close.open")}
          </Button>
        )}
      </div>
      {canWrite && !categoryConfigured && (
        <p className="text-xs text-muted" data-testid="problem-known-error-hint">
          {t("itsm.problem.knownError.noCategory")}
        </p>
      )}
      {knownErrorFailure && (
        <p className="text-xs text-danger" role="alert" data-testid="problem-known-error-error">
          {knownErrorFailure}
        </p>
      )}

      {picking && (
        <IncidentPicker
          ticketId={ticketId}
          linked={new Set(problem.incidents.map((i) => i.ticket_id))}
          onClose={() => setPicking(false)}
          onLinked={() => {
            setPicking(false);
            refresh();
          }}
        />
      )}
      {closing && <CloseIncidentsDialog ticketId={ticketId} count={open.length} onClose={() => setClosing(false)} />}
    </div>
  );
}

/**
 * Problem record of a problem ticket: cause and workaround (editable with `rw`), the linked
 * incidents, the known-error article and "close incidents with workaround". Renders only for
 * `role === "problem"` while the ITSM is enabled.
 */
export function ProblemPanel({ ticket }: { ticket: TicketDetail }) {
  const { t } = useTranslation();
  const itsmQ = useQuery({
    queryKey: itsmKeys.ticketItsm(ticket.id),
    queryFn: ({ signal }) => itsmApi.ticketItsm(ticket.id, signal),
    retry: false,
  });
  const itsm = itsmQ.data;
  if (!itsm || !itsm.enabled || itsm.role !== "problem") return null;
  return (
    <section
      className="space-y-2 rounded-lg border border-hairline bg-surface p-3 text-sm"
      aria-label={t("itsm.problem.title")}
      data-testid="problem-panel"
    >
      <h2 className="text-sm font-medium text-ink">{t("itsm.problem.title")}</h2>
      <ProblemBody ticket={ticket} categoryConfigured={itsm.known_error_category_configured} />
    </section>
  );
}
