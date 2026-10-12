import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Spinner } from "@/components/ui/Spinner";
import { Tabs } from "@/components/ui/Tabs";
import {
  fileToBase64,
  MAX_ZNUNY_FILE_BYTES,
  reportErrorMessage,
  reportsApi,
  type ImportIssue,
  type StatPreview,
  type ZnunyStatsApplyOut,
  type ZnunyStatsPreview,
} from "@/lib/reportsApi";

type Tab = "db" | "file";

function IssueList({ issues }: { issues: ImportIssue[] }) {
  const { t } = useTranslation();
  const groups = [
    ["dropped", issues.filter((i) => i.severity === "dropped"), "dropped"],
    ["approximated", issues.filter((i) => i.severity === "approximated"), "approximated"],
  ] as const;
  return (
    <div className="space-y-2 pl-6 text-xs">
      {groups.map(([sev, items, key]) =>
        items.length === 0 ? null : (
          <div key={sev}>
            <p className="font-semibold text-ink">{t(`reports.znunyImport.${key}`)}</p>
            <ul className="mt-1 space-y-1 text-muted">
              {items.map((i, n) => (
                <li key={n}>
                  <span className="font-mono text-ink">{t("reports.znunyImport.issueLine", { element: i.element, value: i.value })}</span>
                  {" – "}
                  {i.reason}
                </li>
              ))}
            </ul>
          </div>
        ),
      )}
    </div>
  );
}

function StatRow({
  stat,
  selectable,
  checked,
  onToggle,
}: {
  stat: StatPreview;
  selectable: boolean;
  checked: boolean;
  onToggle: () => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const none = stat.target === "none";
  return (
    <li className="rounded-lg border border-hairline bg-surface p-2.5" data-testid={`znuny-import-row-${stat.import_key}`}>
      <div className="flex flex-wrap items-center gap-2">
        {selectable && (
          <input
            type="checkbox"
            className="h-4 w-4"
            checked={checked}
            disabled={none}
            onChange={onToggle}
            aria-label={t("reports.znunyImport.select", { title: stat.title })}
          />
        )}
        <span className="min-w-0 flex-1 text-sm font-medium text-ink">{stat.title}</span>
        <span className="text-xs text-muted">{stat.object_label}</span>
        <Badge tone={none ? "danger" : stat.target === "report" ? "accent" : "default"}>{t(`reports.znunyImport.target.${stat.target}`)}</Badge>
        {stat.already_imported && <Badge tone="muted">{t("reports.znunyImport.alreadyImportedUpdate")}</Badge>}
        {stat.issues.length > 0 && (
          <button
            type="button"
            className="text-xs text-accent underline-offset-2 hover:underline"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
          >
            {t("reports.znunyImport.details")} ({stat.issues.length})
          </button>
        )}
      </div>
      {open && <IssueList issues={stat.issues} />}
    </li>
  );
}

function ResultView({ result }: { result: ZnunyStatsApplyOut }) {
  const { t } = useTranslation();
  return (
    <div className="space-y-3" data-testid="znuny-import-result">
      <p className="text-sm text-ink">{t("reports.znunyImport.imported", { count: result.imported.length })}</p>
      <ul className="space-y-1.5">
        {result.imported.map((r) => (
          <li key={r.import_key} className="flex flex-wrap items-center gap-2 text-sm">
            {r.target === "report" ? (
              <Link to="/agent/reports/$reportKey" params={{ reportKey: String(r.id) }} className="text-accent hover:underline">
                {r.title}
              </Link>
            ) : (
              <Link to="/agent/searches/$searchId" params={{ searchId: String(r.id) }} className="text-accent hover:underline">
                {r.title}
              </Link>
            )}
            <Badge tone="muted">{t(`reports.znunyImport.target.${r.target}`)}</Badge>
            <Badge tone="success">{t(`reports.znunyImport.action.${r.action}`)}</Badge>
          </li>
        ))}
      </ul>
      {result.skipped.length > 0 && (
        <div>
          <p className="text-sm text-muted">{t("reports.znunyImport.skipped", { count: result.skipped.length })}</p>
          <ul className="mt-1 list-disc pl-5 text-xs text-muted">
            {result.skipped.map((s) => (
              <li key={s.import_key}>{s.title}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/**
 * Lists the statistics stored in Znuny (or in an uploaded export file), shows
 * what each becomes in Tiqora and what could not be carried over, and imports
 * the chosen ones. Previewing saves nothing.
 */
export function ZnunyStatsImportDialog({ open, onClose, canCreate }: { open: boolean; onClose: () => void; canCreate: boolean }) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<Tab>("db");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [file, setFile] = useState<File | null>(null);
  const [filePreview, setFilePreview] = useState<ZnunyStatsPreview | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [result, setResult] = useState<ZnunyStatsApplyOut | null>(null);

  const dbQ = useQuery({
    queryKey: ["reports", "znuny-import", "db"],
    queryFn: ({ signal }) => reportsApi.znunyStatsPreview(signal),
    enabled: open,
    staleTime: 0,
    gcTime: 0,
  });

  // Already imported stats start unselected (a re-import overwrites the report's definition).
  const preview = tab === "db" ? dbQ.data : (filePreview ?? undefined);
  const stats = useMemo(() => preview?.stats ?? [], [preview]);
  const importable = useMemo(() => stats.filter((s) => s.target !== "none" && s.stat_id != null), [stats]);

  useEffect(() => {
    if (!open) {
      setResult(null);
      setSelected(new Set());
      setFile(null);
      setFilePreview(null);
      setFileError(null);
      setTab("db");
    }
  }, [open]);

  const fileM = useMutation({
    mutationFn: (f: File) => reportsApi.znunyStatsFilePreview(f),
    onSuccess: (data) => setFilePreview(data),
    onError: (err) => {
      setFilePreview(null);
      setFileError(reportErrorMessage(err, t("reports.znunyImport.fileLoadFailed")));
    },
  });

  const applyM = useMutation({
    mutationFn: async () => {
      if (tab === "file" && file) return reportsApi.znunyStatsApply({ file_content_base64: await fileToBase64(file) });
      return reportsApi.znunyStatsApply({ stat_ids: [...selected].sort((a, b) => a - b) });
    },
    onSuccess: setResult,
  });

  const onPickFile = (f: File | null) => {
    setFilePreview(null);
    setFileError(null);
    setFile(null);
    if (!f) return;
    if (f.size > MAX_ZNUNY_FILE_BYTES) {
      setFileError(t("reports.znunyImport.fileTooLarge"));
      return;
    }
    setFile(f);
    fileM.mutate(f);
  };

  const toggle = (id: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const count = tab === "db" ? selected.size : importable.length;
  const loading = tab === "db" ? dbQ.isPending : fileM.isPending;
  const loadError = tab === "db" && dbQ.error ? reportErrorMessage(dbQ.error, t("reports.znunyImport.loadFailed")) : null;

  const footer = result ? (
    <Button variant="secondary" size="sm" onClick={onClose} data-testid="znuny-import-close">
      {t("reports.znunyImport.close")}
    </Button>
  ) : (
    <div className="flex items-center gap-2">
      {!canCreate && <span className="text-xs text-muted">{t("reports.znunyImport.needsCreate")}</span>}
      <Button
        size="sm"
        onClick={() => applyM.mutate()}
        disabled={!canCreate || count === 0 || applyM.isPending}
        data-testid="znuny-import-apply"
      >
        {applyM.isPending ? t("reports.znunyImport.importing") : t("reports.znunyImport.apply", { count })}
      </Button>
    </div>
  );

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t("reports.znunyImport.title")}
      description={t("reports.znunyImport.intro")}
      size="2xl"
      footer={footer}
    >
      <div data-testid="znuny-import-dialog" className="space-y-3">
        {result ? (
          <ResultView result={result} />
        ) : (
          <>
            <Tabs
              value={tab}
              onChange={(id) => setTab(id as Tab)}
              items={[
                { id: "db", label: t("reports.znunyImport.fromDatabase") },
                { id: "file", label: t("reports.znunyImport.fromFile") },
              ]}
            />
            {tab === "file" && (
              <div className="space-y-1">
                <label className="block text-sm text-ink">
                  {t("reports.znunyImport.chooseFile")}
                  <input
                    type="file"
                    accept=".xml,text/xml,application/xml"
                    className="mt-1 block text-sm"
                    data-testid="znuny-import-file"
                    onChange={(e) => onPickFile(e.target.files?.[0] ?? null)}
                  />
                </label>
                <p className="text-xs text-muted">{t("reports.znunyImport.fileHint")}</p>
                {fileError && (
                  <p role="alert" className="text-sm text-danger">
                    {fileError}
                  </p>
                )}
              </div>
            )}
            {loading ? (
              <Spinner />
            ) : loadError ? (
              <p role="alert" className="text-sm text-danger">
                {loadError}
              </p>
            ) : preview && (tab === "db" || filePreview) ? (
              <>
                {stats.length === 0 && preview.errors.length === 0 ? (
                  <p className="text-sm text-muted" data-testid="znuny-import-empty">
                    {t("reports.znunyImport.empty")}
                  </p>
                ) : (
                  <ul className="space-y-2">
                    {stats.map((s) => (
                      <StatRow
                        key={s.import_key}
                        stat={s}
                        selectable={tab === "db"}
                        checked={s.stat_id != null && selected.has(s.stat_id)}
                        onToggle={() => s.stat_id != null && toggle(s.stat_id)}
                      />
                    ))}
                  </ul>
                )}
                {preview.truncated && <p className="text-xs text-muted">{t("reports.znunyImport.truncated")}</p>}
                {preview.errors.length > 0 && (
                  <div className="space-y-1" data-testid="znuny-import-errors">
                    <p className="text-xs font-semibold text-ink">{t("reports.znunyImport.loadErrors")}</p>
                    <ul className="list-disc pl-5 text-xs text-muted">
                      {preview.errors.map((e, n) => (
                        <li key={n}>{e.stat_id != null ? `#${e.stat_id}: ${e.message}` : e.message}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </>
            ) : null}
            {applyM.isError && (
              <p role="alert" className="text-sm text-danger">
                {reportErrorMessage(applyM.error, t("reports.znunyImport.importFailed"))}
              </p>
            )}
          </>
        )}
      </div>
    </Dialog>
  );
}
