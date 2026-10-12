import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Dialog } from "@/components/ui/Dialog";
import { Spinner } from "@/components/ui/Spinner";
import { JobProgress } from "@/components/jobs/JobProgress";
import { useJob } from "@/components/jobs/useJob";
import { copyText } from "@/components/admin/identity/copyText";
import { apiErrorMessage } from "@/lib/avatarApi";
import { webservicesApi, type DebugContent, type DebugEntry } from "@/lib/webservicesApi";
import { levelTone } from "./debugLevel";
import { prettyBody } from "./prettyBody";

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString();
}

function ContentLine({ line }: { line: DebugContent }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(line.debug_level === "error");
  const [copied, setCopied] = useState<"ok" | "failed" | null>(null);
  const bodyId = `debug-line-body-${line.id}`;

  const copy = async () => {
    setCopied((await copyText(line.content)) ? "ok" : "failed");
    window.setTimeout(() => setCopied(null), 2000);
  };

  return (
    <li className="rounded-lg border border-hairline bg-surface" data-testid={`debug-line-${line.id}`}>
      <div className="flex flex-wrap items-center gap-2 px-3 py-2">
        <button
          type="button"
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
          aria-expanded={open}
          aria-controls={bodyId}
          onClick={() => setOpen((v) => !v)}
          data-testid={`debug-line-toggle-${line.id}`}
        >
          <span aria-hidden className="text-xs text-muted">
            {open ? "▾" : "▸"}
          </span>
          <Badge tone={levelTone(line.debug_level)}>
            {t(`admin.webservices.debugger.levels.${line.debug_level}`, { defaultValue: line.debug_level })}
          </Badge>
          <span className="truncate text-sm font-medium text-ink">{line.subject}</span>
          <span className="shrink-0 text-xs text-muted">{formatTime(line.created)}</span>
          <span className="sr-only">
            {open ? t("admin.webservices.debugger.collapse") : t("admin.webservices.debugger.expand")}
          </span>
        </button>
        {line.masked && (
          <Badge tone="muted" data-testid={`debug-line-masked-${line.id}`}>
            {t("admin.webservices.debugger.masked")}
          </Badge>
        )}
        <Button
          size="sm"
          variant="secondary"
          onClick={() => void copy()}
          data-testid={`debug-line-copy-${line.id}`}
        >
          {copied === "ok"
            ? t("admin.webservices.debugger.copied")
            : copied === "failed"
              ? t("admin.webservices.debugger.copyFailed")
              : t("admin.webservices.debugger.copy")}
        </Button>
      </div>
      {open && (
        <pre
          id={bodyId}
          className="max-h-72 overflow-auto border-t border-hairline bg-surface-subtle p-3 font-mono text-xs text-ink"
          data-testid={`debug-line-content-${line.id}`}
        >
          {prettyBody(line.content)}
        </pre>
      )}
    </li>
  );
}

function ResendFlow({
  wsId,
  entry,
  onResent,
  confirmOpen,
  setConfirmOpen,
}: {
  wsId: number;
  entry: DebugEntry;
  onResent: () => void;
  confirmOpen: boolean;
  setConfirmOpen: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const [jobId, setJobId] = useState<number | null>(null);
  const { job, isTerminal } = useJob(jobId);

  const start = useMutation({
    mutationFn: () => webservicesApi.resend(wsId, entry.id),
    onSuccess: (ref) => {
      setJobId(ref.job_id);
    },
  });

  return (
    <div className="space-y-2" data-testid="debug-resend-flow">
      {jobId == null && (
        <Button
          variant="primary"
          size="sm"
          onClick={() => setConfirmOpen(true)}
          disabled={start.isPending}
          data-testid="debug-resend"
        >
          {t("admin.webservices.debugger.resend")}
        </Button>
      )}
      <ConfirmDialog
        open={confirmOpen}
        title={t("admin.webservices.debugger.resendTitle")}
        message={t("admin.webservices.debugger.resendConfirm")}
        confirmLabel={t("admin.webservices.debugger.resend")}
        pending={start.isPending}
        onCancel={() => setConfirmOpen(false)}
        onConfirm={() => {
          setConfirmOpen(false);
          start.mutate();
        }}
      />
      {start.isError && (
        <p className="text-sm text-danger" role="alert" data-testid="debug-resend-error">
          {apiErrorMessage(start.error, t("admin.webservices.debugger.resendFailed"))}
        </p>
      )}
      {jobId != null && (
        <div className="space-y-2">
          {job ? <JobProgress job={job} testId="debug-resend-job" /> : <Spinner />}
          {isTerminal && job?.status === "succeeded" && (
            <div className="space-y-2" data-testid="debug-resend-done">
              <p className="text-sm text-green" role="status">
                {t("admin.webservices.debugger.resendDone")}
              </p>
              <Button size="sm" variant="secondary" onClick={onResent} data-testid="debug-resend-show-log">
                {t("admin.webservices.debugger.showLog")}
              </Button>
            </div>
          )}
          {isTerminal && job?.status !== "succeeded" && (
            <p className="text-sm text-danger" role="alert" data-testid="debug-resend-job-failed">
              {job?.error ?? t("admin.webservices.debugger.resendJobFailed")}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Side panel with every line of one debugger entry (collapsible, copyable) and,
 * for Tiqora's own outgoing calls, "Erneut senden". Closing returns focus to the
 * row that opened it (the Dialog primitive does that).
 */
export function DebuggerEntryDrawer({
  wsId,
  source,
  entryId,
  onClose,
  onResent,
  startResend = false,
}: {
  wsId: number;
  source: DebugEntry["source"];
  entryId: number;
  onClose: () => void;
  /** The resend job finished and the admin wants to see the log again. */
  onResent: () => void;
  /** Open the confirmation straight away (the row's "Erneut senden" button). */
  startResend?: boolean;
}) {
  const { t } = useTranslation();
  const [confirmOpen, setConfirmOpen] = useState(startResend);
  const q = useQuery({
    queryKey: ["admin", "webservices", wsId, "debugger", source, entryId],
    queryFn: ({ signal }) => webservicesApi.debuggerEntry(wsId, source, entryId, signal),
  });
  const data = q.data;

  return (
    <Dialog
      open
      // Escape inside the confirmation must only dismiss the confirmation.
      onClose={() => {
        if (!confirmOpen) onClose();
      }}
      title={t("admin.webservices.debugger.drawerTitle")}
      description={data ? `${data.entry.name ?? "—"} · ${formatTime(data.entry.created)}` : undefined}
      placement="right"
      size="2xl"
      footer={
        <Button variant="secondary" size="sm" onClick={onClose} data-testid="debug-drawer-close">
          {t("jobs.close")}
        </Button>
      }
    >
      <div className="space-y-4" data-testid="debug-drawer">
        {q.isLoading && <Spinner />}
        {q.isError && (
          <p className="text-sm text-danger" role="alert" data-testid="debug-drawer-error">
            {t("admin.webservices.debugger.entryLoadError")}
          </p>
        )}
        {data && (
          <>
            {data.entry.can_resend && (
              <ResendFlow
                wsId={wsId}
                entry={data.entry}
                onResent={onResent}
                confirmOpen={confirmOpen}
                setConfirmOpen={setConfirmOpen}
              />
            )}
            {data.contents.length === 0 ? (
              <p className="text-sm text-muted">{t("admin.webservices.debugger.noContents")}</p>
            ) : (
              <ul className="space-y-2" data-testid="debug-lines">
                {data.contents.map((line) => (
                  <ContentLine key={line.id} line={line} />
                ))}
              </ul>
            )}
          </>
        )}
      </div>
    </Dialog>
  );
}
