import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ArticleBodyRenderer } from "@/components/agent/ArticleBodyRenderer";
import { ComposerBody } from "@/components/agent/ComposerBody";
import { JobProgress } from "@/components/jobs/JobProgress";
import { useJob } from "@/components/jobs/useJob";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Dialog } from "@/components/ui/Dialog";
import { Spinner } from "@/components/ui/Spinner";
import type { TicketDetail } from "@/lib/api";
import { apiErrorMessage } from "@/lib/avatarApi";
import { formatDateTime } from "@/lib/format";
import { itsmApi, itsmKeys, type MajorIncident } from "@/lib/itsmApi";
import { ticketPerms } from "@/lib/ticket";

type UpdateMode = "internal" | "note" | "reply";
const inputCls =
  "mt-1 w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink focus:border-accent";

function DeclareDialog({
  ticketId,
  onClose,
  onDone,
}: {
  ticketId: number;
  onClose: () => void;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const [summary, setSummary] = useState("");
  const declare = useMutation({
    mutationFn: () => itsmApi.majorIncident.declare(ticketId, summary.trim() || null),
    onSuccess: onDone,
  });
  return (
    <Dialog open onClose={onClose} title={t("itsm.major.declare.title")}>
      <div className="space-y-3" data-testid="major-declare-dialog">
        <label className="block text-xs text-muted">
          {t("itsm.major.declare.summary")}
          <input
            className={inputCls}
            value={summary}
            maxLength={2000}
            onChange={(e) => setSummary(e.target.value)}
            data-testid="major-declare-summary"
          />
        </label>
        {declare.isError && (
          <p className="text-xs text-danger" role="alert" data-testid="major-declare-error">
            {apiErrorMessage(declare.error, t("itsm.major.declare.error"))}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button
            variant="primary"
            size="sm"
            disabled={declare.isPending}
            onClick={() => declare.mutate()}
            data-testid="major-declare-submit"
          >
            {t("itsm.major.declare.submit")}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

function UpdateForm({
  ticketId,
  replyEnabled,
  onPosted,
}: {
  ticketId: number;
  replyEnabled: boolean;
  onPosted: () => void;
}) {
  const { t } = useTranslation();
  const [mode, setMode] = useState<UpdateMode>("internal");
  const [body, setBody] = useState("");
  const [jobId, setJobId] = useState<number | null>(null);
  const { job } = useJob(jobId);
  const post = useMutation({
    mutationFn: () => itsmApi.majorIncident.postUpdate(ticketId, { mode, body_html: body }),
    onSuccess: (out) => {
      setBody("");
      setJobId(out.job_id ?? null);
      onPosted();
    },
  });
  const hasText = body.replace(/<[^>]*>/g, "").trim().length > 0;
  const modes: UpdateMode[] = replyEnabled ? ["internal", "note", "reply"] : ["internal", "note"];
  return (
    <div className="space-y-2" data-testid="major-update-form">
      <h3 className="text-xs font-medium uppercase tracking-wide text-muted">{t("itsm.major.update.title")}</h3>
      <label className="block text-xs text-muted">
        {t("itsm.major.update.mode")}
        <select
          className={inputCls}
          value={mode}
          onChange={(e) => setMode(e.target.value as UpdateMode)}
          data-testid="major-update-mode"
        >
          {modes.map((m) => (
            <option key={m} value={m}>
              {t(`itsm.major.update.modes.${m}`)}
            </option>
          ))}
        </select>
      </label>
      <div className="overflow-hidden rounded-lg border border-hairline bg-surface-subtle/40 focus-within:border-accent/60">
        <ComposerBody richText value={body} onChange={setBody} testId="major-update-body" bare />
      </div>
      {post.isError && (
        <p className="text-xs text-danger" role="alert" data-testid="major-update-error">
          {apiErrorMessage(post.error, t("itsm.major.update.error"))}
        </p>
      )}
      <Button
        size="sm"
        variant="primary"
        disabled={!hasText || post.isPending}
        onClick={() => post.mutate()}
        data-testid="major-update-submit"
      >
        {t("itsm.major.update.send")}
      </Button>
      {job && <JobProgress job={job} testId="major-update-job" />}
    </div>
  );
}

function Timeline({ major }: { major: MajorIncident }) {
  const { t, i18n } = useTranslation();
  return (
    <ol className="space-y-2" data-testid="major-timeline">
      {major.timeline.map((e, idx) => (
        <li key={`${e.kind}-${e.update_id ?? idx}`} className="text-xs" data-testid={`major-timeline-${e.kind}`}>
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={e.kind === "ended" ? "default" : e.kind === "declared" ? "danger" : "accent"}>
              {t(`itsm.major.timeline.${e.kind}`)}
            </Badge>
            <span className="text-muted">{formatDateTime(e.at, i18n.language)}</span>
            {e.kind === "update" && e.mode && (
              <span className="text-muted">
                {t(`itsm.major.update.modes.${e.mode}`)}
                {e.mode !== "internal" && ` (${t("itsm.major.timeline.targets", { count: e.target_count })})`}
              </span>
            )}
          </div>
          {e.summary && <p className="mt-0.5 text-ink">{e.summary}</p>}
          {e.body && (
            <div className="mt-0.5">
              <ArticleBodyRenderer body={e.body} isHtml />
            </div>
          )}
        </li>
      ))}
    </ol>
  );
}

/**
 * Major-incident handling of an incident / major-incident ticket: declare, end, one update for
 * everybody affected, and the timeline. Renders only while the ITSM is enabled.
 */
export function MajorIncidentPanel({ ticket }: { ticket: TicketDetail }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const ticketId = ticket.id;
  const canWrite = ticketPerms(ticket).rw;
  const [declaring, setDeclaring] = useState(false);
  const [ending, setEnding] = useState(false);

  const itsmQ = useQuery({
    queryKey: itsmKeys.ticketItsm(ticketId),
    queryFn: ({ signal }) => itsmApi.ticketItsm(ticketId, signal),
    retry: false,
  });
  const itsm = itsmQ.data;
  const hasRecord = !!itsm?.major_incident;
  const majorQ = useQuery({
    queryKey: itsmKeys.majorIncident(ticketId),
    queryFn: ({ signal }) => itsmApi.majorIncident.get(ticketId, signal),
    enabled: !!itsm?.enabled && hasRecord,
    retry: false,
  });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: itsmKeys.majorIncident(ticketId) });
    void qc.invalidateQueries({ queryKey: itsmKeys.ticketItsm(ticketId) });
    void qc.invalidateQueries({ queryKey: ["tickets", ticketId] });
  };
  const end = useMutation({
    mutationFn: () => itsmApi.majorIncident.end(ticketId),
    onSuccess: () => {
      setEnding(false);
      refresh();
    },
  });

  if (!itsm || !itsm.enabled) return null;
  if (itsm.role !== "incident" && itsm.role !== "major_incident" && !hasRecord) return null;
  const major = majorQ.data ?? itsm.major_incident ?? null;

  return (
    <section
      className="space-y-3 rounded-lg border border-hairline bg-surface p-3 text-sm"
      aria-label={t("itsm.major.title")}
      data-testid="major-panel"
    >
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-medium text-ink">{t("itsm.major.title")}</h2>
        {/* an ended one can be declared again: the backend starts a new run */}
        {canWrite && !major?.active && (
          <Button size="sm" onClick={() => setDeclaring(true)} data-testid="major-declare">
            {t("itsm.major.declare.open")}
          </Button>
        )}
        {canWrite && major?.active && (
          <Button size="sm" variant="secondary" onClick={() => setEnding(true)} data-testid="major-end">
            {t("itsm.major.end.open")}
          </Button>
        )}
      </div>

      {!major ? (
        <p className="text-muted" data-testid="major-none">
          {t("itsm.major.none")}
        </p>
      ) : majorQ.isLoading && !majorQ.data ? (
        <Spinner />
      ) : (
        <>
          <p className="text-ink" data-testid="major-summary">
            <Badge tone={major.active ? "danger" : "default"}>
              {major.active ? t("itsm.major.active") : t("itsm.major.ended")}
            </Badge>{" "}
            {major.summary}
          </p>
          {canWrite && major.active && (
            <UpdateForm ticketId={ticketId} replyEnabled={itsm.major_incident_customer_reply} onPosted={refresh} />
          )}
          <Timeline major={major} />
        </>
      )}

      {declaring && (
        <DeclareDialog
          ticketId={ticketId}
          onClose={() => setDeclaring(false)}
          onDone={() => {
            setDeclaring(false);
            refresh();
          }}
        />
      )}
      <ConfirmDialog
        open={ending}
        title={t("itsm.major.end.title")}
        message={t("itsm.major.end.confirm")}
        confirmLabel={t("itsm.major.end.open")}
        pending={end.isPending}
        onConfirm={() => end.mutate()}
        onCancel={() => setEnding(false)}
      />
      {end.isError && (
        <p className="text-xs text-danger" role="alert" data-testid="major-end-error">
          {apiErrorMessage(end.error, t("itsm.major.end.error"))}
        </p>
      )}
    </section>
  );
}
