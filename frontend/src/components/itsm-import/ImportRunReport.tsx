import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { IMPORT_ISSUE_DISPLAY_LIMIT, IMPORT_STEP_ORDER, type ImportRunOut } from "@/lib/itsmImportApi";
import { jobsApi } from "@/lib/jobsApi";

const CSV_NAME = "itsm-import-bericht.csv";

/** Per-step counters and the first issues of one import run. */
export function ImportRunReport({ run }: { run: ImportRunOut }) {
  const { t } = useTranslation();
  const stepKeys = [
    ...IMPORT_STEP_ORDER.filter((k) => k in run.steps),
    ...Object.keys(run.steps).filter((k) => !(IMPORT_STEP_ORDER as readonly string[]).includes(k)),
  ];
  const shown = run.issues.slice(0, IMPORT_ISSUE_DISPLAY_LIMIT);

  // The CSV lives on the job; the job may be gone (retention), then there is simply no link.
  // Keyed on the run status: the file only appears when the run ends, so a report opened while
  // the run was still going fetches the job again once the run list shows the new status.
  const jobQ = useQuery({
    queryKey: ["admin", "itsm-import", "job", run.job_id, run.status],
    queryFn: ({ signal }) => jobsApi.get(run.job_id as number, signal),
    enabled: run.job_id != null,
    retry: false,
  });
  const csv = jobQ.data?.files.find((f) => f.name === CSV_NAME);

  return (
    <div className="space-y-3" data-testid={`itsm-import-report-${run.id}`}>
      {run.message && <p className="text-sm text-muted">{run.message}</p>}
      {stepKeys.length > 0 && (
        <table className="w-full text-left text-xs">
          <thead className="text-muted">
            <tr>
              <th className="py-1 pr-3 font-medium">{t("admin.itsmImport.report.step")}</th>
              <th className="px-2 py-1 text-right font-medium">{t("admin.itsmImport.report.created")}</th>
              <th className="px-2 py-1 text-right font-medium">{t("admin.itsmImport.report.updated")}</th>
              <th className="px-2 py-1 text-right font-medium">{t("admin.itsmImport.report.unchanged")}</th>
              <th className="px-2 py-1 text-right font-medium">{t("admin.itsmImport.report.skipped")}</th>
              <th className="px-2 py-1 text-right font-medium">{t("admin.itsmImport.report.problems")}</th>
            </tr>
          </thead>
          <tbody>
            {stepKeys.map((k) => {
              const c = run.steps[k]!;
              return (
                <tr key={k} className="border-t border-hairline" data-testid={`itsm-import-step-${k}`}>
                  <td className="py-1 pr-3 text-ink">
                    {t(`admin.itsmImport.steps.${k}`, { defaultValue: k })}
                  </td>
                  <td className="px-2 py-1 text-right tabular-nums">{c.created}</td>
                  <td className="px-2 py-1 text-right tabular-nums">{c.updated}</td>
                  <td className="px-2 py-1 text-right tabular-nums">{c.unchanged}</td>
                  <td className="px-2 py-1 text-right tabular-nums">{c.skipped}</td>
                  <td className={`px-2 py-1 text-right tabular-nums ${c.problems > 0 ? "text-danger" : ""}`}>
                    {c.problems}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {shown.length === 0 ? (
        <p className="text-sm text-muted" data-testid="itsm-import-no-issues">
          {t("admin.itsmImport.report.noIssues")}
        </p>
      ) : (
        <div className="space-y-1">
          <p className="text-xs font-semibold text-ink">
            {t("admin.itsmImport.report.issues", { count: run.issue_count })}
          </p>
          <ul className="space-y-1 text-xs" data-testid="itsm-import-issues">
            {shown.map((i, n) => (
              <li key={n} className="rounded border border-hairline p-2">
                <span className="font-medium text-ink">
                  {t(`admin.itsmImport.steps.${i.step}`, { defaultValue: i.step })}
                </span>
                {i.object_ref && <span className="text-muted"> · {i.object_ref}</span>}
                <div className="text-ink">{i.explanation || i.code}</div>
                {i.message && <div className="text-muted">{i.message}</div>}
              </li>
            ))}
          </ul>
          {run.issue_count > shown.length && (
            <p className="text-xs text-muted">
              {t("admin.itsmImport.report.truncated", { shown: shown.length, count: run.issue_count })}
            </p>
          )}
        </div>
      )}

      {run.job_id != null && csv && (
        <a
          href={jobsApi.fileUrl(run.job_id, csv.id)}
          className="text-sm text-accent hover:underline"
          data-testid="itsm-import-csv"
        >
          {t("admin.itsmImport.report.csv")}
        </a>
      )}
    </div>
  );
}
