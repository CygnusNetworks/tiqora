import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useJob } from "@/components/jobs/useJob";
import { JobProgress } from "@/components/jobs/JobProgress";
import { Button } from "@/components/ui/Button";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { SelectField } from "@/components/ui/SelectField";
import { Spinner } from "@/components/ui/Spinner";
import { Switch } from "@/components/ui/Switch";
import { apiErrorMessage } from "@/lib/avatarApi";
import { cmdbApi, type CmdbClass, type CmdbImportRequest, type ImportTemplate } from "@/lib/cmdbApi";
import { jobsApi, type Job } from "@/lib/jobsApi";
import { MappingEditor } from "./MappingEditor";
import {
  INPUT_FILE_PREFIX,
  REPORT_NAME,
  REPORT_ROWS,
  applyTemplate,
  countsOf,
  guessDelimiter,
  identifierOptions,
  importAllowed,
  parseReport,
  readCsvPreview,
  runFingerprint,
  suggestMapping,
  targetOptions,
  templateCsv,
  toMappingIn,
  validateMapping,
  type CsvDelimiter,
  type CsvEncoding,
  type DryRun,
  type MappingRow,
} from "./mappingModel";

type Step = 1 | 2 | 3 | 4;
type OnError = "abort" | "skip";
type EmptyCells = "keep" | "clear";

const INPUT_CLS =
  "w-full rounded border border-hairline bg-surface px-2 py-1.5 text-sm text-ink focus:outline-none focus:ring-1 focus:ring-accent";
const LABEL_CLS = "mb-1 block text-xs font-medium text-muted";

function formatOf(file: File): "csv" | "xlsx" {
  return file.name.toLowerCase().endsWith(".xlsx") ? "xlsx" : "csv";
}

/** The job's own output files; the uploaded input file is no download. */
function visibleJob(job: Job): Job {
  return { ...job, files: job.files.filter((f) => !f.name.startsWith(INPUT_FILE_PREFIX)) };
}

const STATUS_CLS: Record<string, string> = {
  created: "text-ink",
  updated: "text-ink",
  unchanged: "text-muted",
  skipped: "text-muted",
  error: "text-danger",
};

/** First rows of the job's report, read from its `report.csv`. */
function ReportTable({ job }: { job: Job }) {
  const { t } = useTranslation();
  const file = job.files.find((f) => f.name === REPORT_NAME);
  const q = useQuery({
    queryKey: ["cmdb", "import-report", job.id, file?.id],
    enabled: !!file,
    retry: false,
    queryFn: async () => {
      const res = await fetch(jobsApi.fileUrl(job.id, file!.id), { credentials: "same-origin" });
      if (!res.ok) throw new Error(String(res.status));
      return parseReport(await res.text(), REPORT_ROWS);
    },
  });
  if (!file) return null;
  return (
    <div className="space-y-2" data-testid="import-report">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-ink">{t("cmdb.import.report.title", { count: REPORT_ROWS })}</h3>
        <a
          href={jobsApi.fileUrl(job.id, file.id)}
          download
          className="text-sm text-accent hover:underline"
          data-testid="import-report-download"
        >
          {t("cmdb.import.report.download")}
        </a>
      </div>
      {q.isPending ? (
        <Spinner />
      ) : q.isError ? (
        <p className="text-xs text-muted">{t("cmdb.import.report.loadFailed")}</p>
      ) : q.data.length === 0 ? (
        <p className="text-xs text-muted">{t("cmdb.import.report.empty")}</p>
      ) : (
        <div className="max-h-80 overflow-auto rounded-md border border-hairline">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-surface-subtle text-left text-xs text-muted">
              <tr>
                <th className="px-3 py-1.5 font-medium">{t("cmdb.import.report.row")}</th>
                <th className="px-3 py-1.5 font-medium">{t("cmdb.import.report.status")}</th>
                <th className="px-3 py-1.5 font-medium">{t("cmdb.import.report.ci")}</th>
                <th className="px-3 py-1.5 font-medium">{t("cmdb.import.report.message")}</th>
              </tr>
            </thead>
            <tbody>
              {q.data.map((r, i) => (
                <tr key={i} className="border-t border-hairline align-top" data-testid="import-report-row">
                  <td className="px-3 py-1.5 tabular-nums">{r.row}</td>
                  <td className={`px-3 py-1.5 ${STATUS_CLS[r.status] ?? ""}`}>
                    {STATUS_CLS[r.status] ? t(`cmdb.import.report.statuses.${r.status}`) : r.status}
                  </td>
                  <td className="px-3 py-1.5">{r.ciNumber}</td>
                  <td className="px-3 py-1.5">{r.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/** Following one import job: progress, then the result (or the error) and the report. */
function RunPanel({ jobId, dryRun, testId, onResult }: { jobId: number; dryRun: boolean; testId: string; onResult?: (job: Job) => void }) {
  const { t } = useTranslation();
  const { job, isTerminal, error } = useJob(jobId);
  const notified = useRef<number | null>(null);
  const cb = useRef(onResult);
  cb.current = onResult;
  useEffect(() => {
    if (job && isTerminal && notified.current !== job.id) {
      notified.current = job.id;
      cb.current?.(job);
    }
  }, [job, isTerminal]);

  if (!job) return error ? <p className="text-sm text-danger" role="alert">{t("jobs.loadError")}</p> : <Spinner />;
  const counts = countsOf(job.result);
  return (
    <div className="space-y-3" data-testid={testId}>
      <JobProgress job={visibleJob(job)} testId={`${testId}-progress`} />
      {job.status === "succeeded" && (
        <dl className="grid grid-cols-2 gap-2 sm:grid-cols-5" data-testid={`${testId}-counts`}>
          {(["created", "updated", "unchanged", "errors", "skipped"] as const).map((k) => (
            <div key={k} className="rounded-md border border-hairline px-3 py-2">
              <dt className="text-xs text-muted">{t(`cmdb.import.counts.${k}`)}</dt>
              <dd className={`text-lg font-semibold tabular-nums ${k === "errors" && counts.errors > 0 ? "text-danger" : "text-ink"}`} data-testid={`${testId}-count-${k}`}>
                {counts[k]}
              </dd>
            </div>
          ))}
        </dl>
      )}
      {job.status === "failed" && (
        <p className="text-sm text-danger" role="alert" data-testid={`${testId}-failed`}>
          {dryRun ? t("cmdb.import.dry.failed") : t("cmdb.import.run.failed")}
        </p>
      )}
      {isTerminal && <ReportTable job={job} />}
    </div>
  );
}

/**
 * Four steps: file, mapping, dry run, import. The real import starts only after a dry run of the
 * same file and mapping that had no errors (or that ran with "skip faulty rows").
 */
export function ImportWizard({ classes, initialClassId }: { classes: CmdbClass[]; initialClassId?: number }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [step, setStep] = useState<Step>(1);

  // step 1: file
  const [classId, setClassId] = useState<number | null>(initialClassId ?? null);
  const [file, setFile] = useState<File | null>(null);
  const [buffer, setBuffer] = useState<ArrayBuffer | null>(null);
  const [encoding, setEncoding] = useState<CsvEncoding>("utf-8");
  const [delimiter, setDelimiter] = useState<CsvDelimiter>(";");
  const [delimiterTouched, setDelimiterTouched] = useState(false);
  const [sheet, setSheet] = useState("");
  const [templateId, setTemplateId] = useState<number | null>(null);

  // step 2: mapping
  const [rows, setRows] = useState<MappingRow[]>([]);
  const [identifiers, setIdentifiers] = useState<string[]>(["number"]);
  // the identifier follows the mapping until the user (or a template) has chosen one
  const identTouched = useRef(false);
  const [emptyCells, setEmptyCells] = useState<EmptyCells>("keep");
  const [onError, setOnError] = useState<OnError>("abort");
  const [createMissing, setCreateMissing] = useState(true);
  const [templateName, setTemplateName] = useState("");
  const [templateMsg, setTemplateMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  // steps 3/4: jobs
  const [dryJob, setDryJob] = useState<{ id: number; fingerprint: string } | null>(null);
  const [dryRun, setDryRun] = useState<DryRun | null>(null);
  const [importJob, setImportJob] = useState<number | null>(null);
  const [startError, setStartError] = useState<string | null>(null);

  const format = file ? formatOf(file) : "csv";
  const cls = classes.find((c) => c.id === classId) ?? null;

  const defQ = useQuery({
    queryKey: ["cmdb", "class-definition", cls?.id, cls?.current_definition_version],
    queryFn: () => cmdbApi.definition(cls!.id, cls!.current_definition_version!),
    enabled: !!cls && !cls.definition && cls.current_definition_version != null,
  });
  const defs = useMemo(() => (cls?.definition ?? defQ.data)?.attributes ?? [], [cls, defQ.data]);
  const options = useMemo(() => targetOptions(defs), [defs]);
  const identOptions = useMemo(() => identifierOptions(defs), [defs]);

  const templatesQ = useQuery({
    queryKey: ["cmdb", "import-templates", classId],
    queryFn: () => cmdbApi.importTemplates(classId!),
    enabled: classId != null,
  });
  const templates: ImportTemplate[] = templatesQ.data ?? [];
  const template = templates.find((x) => x.id === templateId) ?? null;

  // the file's bytes (the CSV preview reads them again whenever encoding or delimiter change)
  useEffect(() => {
    let alive = true;
    setBuffer(null);
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      if (alive && reader.result instanceof ArrayBuffer) setBuffer(reader.result);
    };
    reader.readAsArrayBuffer(file);
    return () => {
      alive = false;
    };
  }, [file]);

  // pick the delimiter from the first line until the user chooses one
  useEffect(() => {
    if (!buffer || format !== "csv" || delimiterTouched) return;
    try {
      setDelimiter(guessDelimiter(new TextDecoder(encoding === "cp1252" ? "windows-1252" : "utf-8").decode(buffer.slice(0, 4096))));
    } catch {
      /* the preview reports an undecodable file */
    }
  }, [buffer, encoding, format, delimiterTouched]);

  const preview = useMemo(
    () => (buffer && file && format === "csv" ? readCsvPreview(buffer, encoding, delimiter) : null),
    [buffer, file, format, encoding, delimiter],
  );
  const headerKey = preview && !preview.error ? JSON.stringify(preview.header) : format === "xlsx" ? "xlsx" : "";

  // rows from a template or from the suggestion, whenever the header or the template changes
  useEffect(() => {
    if (!file) {
      setRows([]);
      return;
    }
    if (format === "xlsx") {
      // an Excel header is read by the server; without it the stored template rows are all we know
      setRows(template ? template.mapping.map((m) => ({ column: String(m.column), target: m.target === "ignore" ? "" : m.target })) : []);
    } else if (preview && !preview.error) {
      setRows(template ? applyTemplate(preview.header, template.mapping) : suggestMapping(preview.header, defs));
    } else setRows([]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [headerKey, templateId, defs, file]);

  // a template also brings its options
  useEffect(() => {
    if (!template) return;
    const o = template.options;
    identTouched.current = true;
    setIdentifiers(o.identifiers ?? ["number"]);
    setEmptyCells(o.empty_cells);
    setOnError(o.on_error);
    setCreateMissing(o.create_missing);
    if (o.sheet) setSheet(o.sheet);
    if (format === "csv") {
      setDelimiter(o.delimiter);
      setDelimiterTouched(true);
      setEncoding(o.encoding);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [templateId]);

  // without a template the identifier follows the mapping: number if it is there, else name
  const mappedKey = rows.map((r) => r.target).join("|");
  useEffect(() => {
    if (template || identTouched.current) return;
    if (rows.some((r) => r.target === "number")) setIdentifiers(["number"]);
    else if (rows.some((r) => r.target === "name")) setIdentifiers(["name"]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mappedKey]);

  const importOptions = useMemo(
    () => ({
      delimiter,
      encoding,
      header_row: true,
      sheet: format === "xlsx" && sheet.trim() ? sheet.trim() : null,
      identifiers,
      empty_cells: emptyCells,
      on_error: onError,
      create_missing: createMissing,
    }),
    [delimiter, encoding, format, sheet, identifiers, emptyCells, onError, createMissing],
  );
  const mappingIn = useMemo(() => (rows.length > 0 ? toMappingIn(rows) : []), [rows]);
  const fingerprint = runFingerprint({
    file: file ? { name: file.name, size: file.size, lastModified: file.lastModified } : null,
    classId,
    format,
    options: importOptions,
    mapping: mappingIn,
  });

  // an Excel header is unknown here: the server maps the headers that are targets themselves, or the template does
  const problems = rows.length === 0 && format === "xlsx" ? [] : validateMapping(rows, identifiers, { createMissing });
  const step1Ok = !!cls && !!file && !!buffer && (format === "xlsx" || (preview != null && !preview.error));
  const allowed = importAllowed(dryRun, fingerprint, onError);
  const dryStale = dryRun != null && dryRun.fingerprint !== fingerprint;

  const request = (dry: boolean): CmdbImportRequest => ({
    class_id: classId,
    file_format: format,
    options: importOptions,
    mapping: mappingIn.length > 0 ? mappingIn : null,
    dry_run: dry,
    time_zone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
  });

  const start = useMutation({
    mutationFn: async (dry: boolean) => ({ dry, accepted: await cmdbApi.startImport(file!, request(dry)), fingerprint }),
    onMutate: () => setStartError(null),
    onSuccess: ({ dry, accepted, fingerprint: fp }) => {
      if (dry) {
        setDryRun(null);
        setDryJob({ id: accepted.job_id, fingerprint: fp });
      } else setImportJob(accepted.job_id);
    },
    onError: (err) => setStartError(apiErrorMessage(err, t("cmdb.import.startFailed"))),
  });

  const saveTemplate = useMutation({
    mutationFn: () =>
      cmdbApi.createImportTemplate({
        class_id: classId!,
        file_format: format,
        name: templateName.trim(),
        mapping: mappingIn,
        options: importOptions,
        shared: false,
      }),
    onSuccess: (saved) => {
      setTemplateMsg({ kind: "ok", text: t("cmdb.import.template.saved", { name: saved.name }) });
      setTemplateName("");
      void qc.invalidateQueries({ queryKey: ["cmdb", "import-templates"] });
    },
    onError: (err) => setTemplateMsg({ kind: "error", text: apiErrorMessage(err, t("cmdb.import.template.saveFailed")) }),
  });

  const downloadTemplate = () => {
    const blob = new Blob([templateCsv(defs)], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${cls?.class_key ?? "cmdb"}-import-vorlage.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const onDryResult = (job: Job) => {
    if (job.status === "succeeded" && dryJob && job.id === dryJob.id) setDryRun({ fingerprint: dryJob.fingerprint, counts: countsOf(job.result) });
  };

  const steps: Array<{ n: Step; label: string }> = [
    { n: 1, label: t("cmdb.import.steps.file") },
    { n: 2, label: t("cmdb.import.steps.mapping") },
    { n: 3, label: t("cmdb.import.steps.dry") },
    { n: 4, label: t("cmdb.import.steps.run") },
  ];
  const toggleIdentifier = (id: string) => {
    identTouched.current = true;
    setIdentifiers((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : prev.length >= 5 ? prev : [...prev, id]));
  };

  return (
    <div className="min-w-0 space-y-4" data-testid="import-wizard">
      <ol className="flex flex-wrap gap-2 text-sm" aria-label={t("cmdb.import.steps.label")} data-testid="import-steps">
        {steps.map((s) => (
          <li
            key={s.n}
            aria-current={step === s.n ? "step" : undefined}
            className={`rounded-full border px-3 py-1 ${step === s.n ? "border-accent bg-accent text-accent-ink" : "border-hairline text-muted"}`}
            data-testid={`import-step-${s.n}`}
          >
            {s.n}. {s.label}
          </li>
        ))}
      </ol>

      {step === 1 && (
        <section className="space-y-4" data-testid="import-step-file">
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label className={LABEL_CLS} htmlFor="import-class">
                {t("cmdb.import.file.class")}
              </label>
              <SelectField<number>
                id="import-class"
                testId="import-class"
                value={classId}
                placeholder={t("cmdb.import.file.classPlaceholder")}
                items={classes.map((c) => ({ value: c.id, label: c.name }))}
                onChange={(v) => {
                  setClassId(v);
                  setTemplateId(null);
                  setDryRun(null);
                }}
              />
            </div>
            <div>
              <label className={LABEL_CLS} htmlFor="import-template">
                {t("cmdb.import.file.template")}
              </label>
              <SelectField<number>
                id="import-template"
                testId="import-template"
                value={templateId}
                disabled={templates.length === 0}
                placeholder={templates.length === 0 ? t("cmdb.import.file.noTemplates") : t("cmdb.import.file.templatePlaceholder")}
                items={templates.map((x) => ({ value: x.id, label: x.name, hint: x.shared && !x.owned ? t("cmdb.import.template.shared") : undefined }))}
                onChange={setTemplateId}
              />
              {template && (
                <button type="button" className="mt-1 text-xs text-accent hover:underline" onClick={() => setTemplateId(null)} data-testid="import-template-clear">
                  {t("cmdb.import.file.noTemplate")}
                </button>
              )}
            </div>
          </div>

          <div>
            <label className={LABEL_CLS} htmlFor="import-file">
              {t("cmdb.import.file.file")}
            </label>
            <input
              id="import-file"
              type="file"
              accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              className="block w-full text-sm text-ink file:mr-3 file:rounded file:border file:border-hairline file:bg-surface file:px-3 file:py-1.5 file:text-sm"
              data-testid="import-file"
              onChange={(e) => {
                setFile(e.target.files?.[0] ?? null);
                setDelimiterTouched(false);
                setDryRun(null);
              }}
            />
            <p className="mt-1 text-xs text-muted">{t("cmdb.import.file.hint")}</p>
          </div>

          {file && format === "csv" && (
            <div className="grid gap-4 sm:grid-cols-2" data-testid="import-csv-options">
              <div>
                <span className={LABEL_CLS} id="import-encoding-label">
                  {t("cmdb.import.file.encoding")}
                </span>
                <SegmentedControl<CsvEncoding>
                  testId="import-encoding"
                  aria-labelledby="import-encoding-label"
                  value={encoding}
                  items={[
                    { value: "utf-8", label: "UTF-8" },
                    { value: "cp1252", label: "Windows-1252" },
                  ]}
                  onChange={setEncoding}
                />
              </div>
              <div>
                <span className={LABEL_CLS} id="import-delimiter-label">
                  {t("cmdb.import.file.delimiter")}
                </span>
                <SegmentedControl<CsvDelimiter>
                  testId="import-delimiter"
                  aria-labelledby="import-delimiter-label"
                  value={delimiter}
                  items={[
                    { value: ";", label: t("cmdb.import.file.delimiters.semicolon") },
                    { value: ",", label: t("cmdb.import.file.delimiters.comma") },
                    { value: "\t", label: t("cmdb.import.file.delimiters.tab") },
                    { value: "|", label: t("cmdb.import.file.delimiters.pipe") },
                  ]}
                  onChange={(d) => {
                    setDelimiter(d);
                    setDelimiterTouched(true);
                  }}
                />
              </div>
            </div>
          )}
          {file && format === "xlsx" && (
            <div>
              <label className={LABEL_CLS} htmlFor="import-sheet">
                {t("cmdb.import.file.sheet")}
              </label>
              <input id="import-sheet" className={INPUT_CLS} value={sheet} onChange={(e) => setSheet(e.target.value)} placeholder={t("cmdb.import.file.sheetPlaceholder")} data-testid="import-sheet" />
              <p className="mt-1 text-xs text-muted">{t("cmdb.import.file.xlsxHint")}</p>
            </div>
          )}
          {preview?.error && (
            <p role="alert" className="text-sm text-danger" data-testid="import-file-error">
              {preview.error === "decode" ? t("cmdb.import.file.decodeFailed") : t("cmdb.import.file.empty")}
            </p>
          )}

          <div className="flex flex-wrap items-center justify-between gap-2">
            <Button variant="secondary" onClick={downloadTemplate} disabled={!cls || defs.length === 0} data-testid="import-download-template">
              {t("cmdb.import.file.downloadTemplate")}
            </Button>
            <Button variant="primary" onClick={() => setStep(2)} disabled={!step1Ok} data-testid="import-next-1">
              {t("cmdb.import.next")}
            </Button>
          </div>
        </section>
      )}

      {step === 2 && (
        <section className="space-y-4" data-testid="import-step-mapping">
          {format === "xlsx" && rows.length === 0 ? (
            <p className="rounded-md border border-hairline bg-surface-subtle px-4 py-3 text-sm text-ink" data-testid="import-xlsx-notice">
              {t("cmdb.import.map.xlsxNotice")}
            </p>
          ) : (
            <MappingEditor rows={rows} samples={format === "csv" ? (preview?.samples ?? null) : null} options={options} onChange={setRows} />
          )}

          <div className="grid gap-4 sm:grid-cols-2">
            <fieldset>
              <legend className={LABEL_CLS}>{t("cmdb.import.map.identifier")}</legend>
              <div className="flex flex-wrap gap-x-4 gap-y-1">
                {identOptions.map((id) => (
                  <label key={id} className="flex items-center gap-1.5 text-sm text-ink">
                    <input type="checkbox" checked={identifiers.includes(id)} onChange={() => toggleIdentifier(id)} data-testid={`import-ident-${id}`} />
                    {id === "number" || id === "name" ? t(`cmdb.import.fixed.${id}`) : (options.find((o) => o.value === id)?.label ?? id)}
                  </label>
                ))}
              </div>
              <p className="mt-1 text-xs text-muted">{t("cmdb.import.map.identifierHint")}</p>
            </fieldset>
            <div className="space-y-3">
              <div>
                <span className={LABEL_CLS} id="import-empty-label">
                  {t("cmdb.import.map.emptyCells")}
                </span>
                <SegmentedControl<EmptyCells>
                  testId="import-empty-cells"
                  aria-labelledby="import-empty-label"
                  value={emptyCells}
                  items={[
                    { value: "keep", label: t("cmdb.import.map.emptyKeep") },
                    { value: "clear", label: t("cmdb.import.map.emptyClear") },
                  ]}
                  onChange={setEmptyCells}
                />
              </div>
              <div>
                <span className={LABEL_CLS} id="import-onerror-label">
                  {t("cmdb.import.map.onError")}
                </span>
                <SegmentedControl<OnError>
                  testId="import-on-error"
                  aria-labelledby="import-onerror-label"
                  value={onError}
                  items={[
                    { value: "abort", label: t("cmdb.import.map.onErrorAbort") },
                    { value: "skip", label: t("cmdb.import.map.onErrorSkip") },
                  ]}
                  onChange={setOnError}
                />
              </div>
              <label className="flex items-center gap-2 text-sm text-ink">
                <Switch checked={createMissing} onChange={setCreateMissing} testId="import-create-missing" aria-label={t("cmdb.import.map.createMissing")} />
                {t("cmdb.import.map.createMissing")}
              </label>
            </div>
          </div>

          {problems.length > 0 && (
            <ul role="alert" className="list-disc space-y-0.5 pl-5 text-sm text-danger" data-testid="import-problems">
              {problems.map((p) => (
                <li key={p}>{t(p)}</li>
              ))}
            </ul>
          )}

          <div className="space-y-2 rounded-md border border-hairline p-3" data-testid="import-template-save">
            <span className={LABEL_CLS}>{t("cmdb.import.template.saveTitle")}</span>
            <div className="flex flex-wrap items-center gap-2">
              <input
                className={`${INPUT_CLS} max-w-xs`}
                value={templateName}
                onChange={(e) => setTemplateName(e.target.value)}
                placeholder={t("cmdb.import.template.namePlaceholder")}
                aria-label={t("cmdb.import.template.name")}
                maxLength={100}
                data-testid="import-template-name"
              />
              <Button
                variant="secondary"
                onClick={() => {
                  setTemplateMsg(null);
                  saveTemplate.mutate();
                }}
                disabled={templateName.trim() === "" || problems.length > 0 || rows.length === 0 || saveTemplate.isPending}
                data-testid="import-template-save-btn"
              >
                {t("cmdb.import.template.save")}
              </Button>
            </div>
            {templateMsg && (
              <p role={templateMsg.kind === "error" ? "alert" : "status"} className={`text-xs ${templateMsg.kind === "error" ? "text-danger" : "text-muted"}`} data-testid="import-template-msg">
                {templateMsg.text}
              </p>
            )}
          </div>

          <div className="flex items-center justify-between gap-2">
            <Button variant="secondary" onClick={() => setStep(1)} data-testid="import-back-2">
              {t("cmdb.import.back")}
            </Button>
            <Button variant="primary" onClick={() => setStep(3)} disabled={problems.length > 0} data-testid="import-next-2">
              {t("cmdb.import.next")}
            </Button>
          </div>
        </section>
      )}

      {step === 3 && (
        <section className="space-y-4" data-testid="import-step-dry">
          <p className="text-sm text-muted">{t("cmdb.import.dry.intro")}</p>
          {startError && !importJob && (
            <p role="alert" className="text-sm text-danger" data-testid="import-start-error">
              {startError}
            </p>
          )}
          {dryJob && <RunPanel key={dryJob.id} jobId={dryJob.id} dryRun testId="import-dry" onResult={onDryResult} />}
          {dryStale && (
            <p role="status" className="text-sm text-warn" data-testid="import-dry-stale">
              {t("cmdb.import.dry.stale")}
            </p>
          )}
          {dryRun && !dryStale && dryRun.counts.errors > 0 && onError === "abort" && (
            <p role="status" className="text-sm text-warn" data-testid="import-dry-blocked">
              {t("cmdb.import.dry.blocked")}
            </p>
          )}
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Button variant="secondary" onClick={() => setStep(2)} data-testid="import-back-3">
              {t("cmdb.import.back")}
            </Button>
            <div className="flex gap-2">
              <Button variant="secondary" onClick={() => start.mutate(true)} disabled={start.isPending} data-testid="import-dry-start">
                {dryJob ? t("cmdb.import.dry.again") : t("cmdb.import.dry.start")}
              </Button>
              <Button variant="primary" onClick={() => setStep(4)} disabled={!allowed} data-testid="import-next-3">
                {t("cmdb.import.next")}
              </Button>
            </div>
          </div>
        </section>
      )}

      {step === 4 && (
        <section className="space-y-4" data-testid="import-step-run">
          {importJob == null ? (
            <>
              <p className="text-sm text-ink" data-testid="import-run-summary">
                {t("cmdb.import.run.summary", {
                  created: dryRun?.counts.created ?? 0,
                  updated: dryRun?.counts.updated ?? 0,
                  errors: dryRun?.counts.errors ?? 0,
                })}
              </p>
              {startError && (
                <p role="alert" className="text-sm text-danger" data-testid="import-start-error">
                  {startError}
                </p>
              )}
              <div className="flex items-center justify-between gap-2">
                <Button variant="secondary" onClick={() => setStep(3)} data-testid="import-back-4">
                  {t("cmdb.import.back")}
                </Button>
                <Button variant="primary" onClick={() => start.mutate(false)} disabled={!allowed || start.isPending} data-testid="import-run-start">
                  {t("cmdb.import.run.start")}
                </Button>
              </div>
            </>
          ) : (
            <>
              <RunPanel
                key={importJob}
                jobId={importJob}
                dryRun={false}
                testId="import-run"
                onResult={(job) => {
                  if (job.status === "succeeded") void qc.invalidateQueries({ queryKey: ["cmdb", "cis"] });
                }}
              />
              <Link to="/agent/cmdb" search={classId ? { class: classId } : {}} className="inline-block text-sm text-accent hover:underline" data-testid="import-to-list">
                {t("cmdb.import.run.toList")}
              </Link>
            </>
          )}
        </section>
      )}
    </div>
  );
}
