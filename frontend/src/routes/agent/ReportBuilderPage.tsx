import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import { labelKeysOf, usePluginReportTexts } from "@/components/reports/usePluginReportTexts";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";
import { BuilderFieldsPanel } from "@/components/reports/builder/BuilderFieldsPanel";
import { BuilderOptionsPanel } from "@/components/reports/builder/BuilderOptionsPanel";
import { BuilderPreview } from "@/components/reports/builder/BuilderPreview";
import { SaveReportDialog, type SaveError } from "@/components/reports/builder/SaveReportDialog";
import type { ShareValue } from "@/components/reports/builder/ShareFields";
import {
  builderReducer,
  fromDefinition,
  initialState,
  toDefinition,
  validate,
  type BuilderState,
} from "@/components/reports/builder/builderState";
import { inputCls } from "@/components/reports/builder/builderUi";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Spinner } from "@/components/ui/Spinner";
import { ApiError } from "@/lib/api";
import {
  isSavedKey,
  reportDescription,
  reportErrorMessage,
  reportsApi,
  reportTitle,
  type ReportItem,
  type SavedReportIn,
} from "@/lib/reportsApi";
import type { ReportBuilderSearch } from "./reportBuilderSearch";

type Mode = "new" | "edit";

const PRIVATE_SHARE: ShareValue = { visibility: "private", group_ids: [] };

function Notice({ testId, children }: { testId: string; children: React.ReactNode }) {
  return (
    <div className="m-6 rounded-lg border border-hairline bg-surface p-8 text-center" data-testid={testId}>
      {children}
    </div>
  );
}

/** What the editor starts from: the loaded report (edit), a copy of one (`from`), or nothing. */
type Start = {
  state: BuilderState;
  share: ShareValue;
  /** Key the new report is a copy of. */
  copiedFrom: string | null;
};

function startFrom(mode: Mode, report: ReportItem | undefined, t: ReturnType<typeof useTranslation>["t"]): Start | null {
  if (!report) return { state: initialState(), share: { visibility: "private", group_ids: [] }, copiedFrom: null };
  if (!report.definition) return null;
  if (mode === "edit") {
    return {
      state: fromDefinition(report.definition, { name: report.name ?? "", description: report.description ?? "" }),
      share: { visibility: report.visibility ?? "private", group_ids: report.group_ids ?? [] },
      copiedFrom: report.copied_from ?? null,
    };
  }
  return {
    state: fromDefinition(report.definition, {
      name: t("reports.builder.copyOf", { name: reportTitle(report, t) }),
      description: reportDescription(report, t),
    }),
    share: { visibility: "private", group_ids: [] },
    copiedFrom: report.key,
  };
}

function Editor({
  mode,
  start,
  report,
  onReload,
}: {
  mode: Mode;
  start: Start;
  /** The saved report being edited (edit mode). */
  report: ReportItem | undefined;
  onReload: () => void;
}) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [state, dispatch] = useReducer(builderReducer, start.state);
  const baseline = useRef(JSON.stringify(start.state));
  const [share, setShare] = useState<ShareValue>(start.share);
  // "Als Kopie speichern" has its own sharing: without the share right a copy starts as "Nur ich"
  // (the locked choice would otherwise carry the original's sharing into a 403), and the
  // original's sharing stays untouched for a later "Speichern".
  const [copyShare, setCopyShare] = useState<ShareValue>(PRIVATE_SHARE);
  const shareBaseline = useRef(JSON.stringify(start.share));
  const [dialog, setDialog] = useState<"create" | "update" | "copy" | null>(null);
  const [tried, setTried] = useState(false);
  const [saveError, setSaveError] = useState<SaveError | null>(null);
  const [leaving, setLeaving] = useState(false);
  const [done, setDone] = useState(false);
  const [fileError, setFileError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement | null>(null);

  const sourcesQ = useQuery({
    queryKey: ["reports", "sources"],
    queryFn: ({ signal }) => reportsApi.sources(signal),
    staleTime: 5 * 60_000,
  });
  const sources = sourcesQ.data ?? [];
  const source = sources.find((s) => s.key === state.source);

  const issues = useMemo(() => validate(state, source), [state, source]);
  const definition = useMemo(() => (issues.length === 0 ? toDefinition(state) : null), [issues, state]);
  const canShare = user?.can_share_reports ?? false;
  const dirty = !done && (JSON.stringify(state) !== baseline.current || JSON.stringify(share) !== shareBaseline.current);

  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const activeShare = dialog === "copy" ? copyShare : share;
  const groupsMissing = activeShare.visibility === "groups" && activeShare.group_ids.length === 0;
  const saveIssues = validate(state, source, { name: true, groupsMissing }).filter(
    (i) => i.key === "nameRequired" || i.key === "groupsRequired",
  );

  const save = useMutation({
    mutationFn: async (kind: "create" | "update" | "copy") => {
      const defn = toDefinition(state);
      if (!defn) throw new Error(t("reports.builder.previewInvalid"));
      const sharing = kind === "copy" ? copyShare : share;
      const body: SavedReportIn = {
        name: state.name.trim(),
        description: state.description,
        definition: defn,
        visibility: sharing.visibility,
        group_ids: sharing.visibility === "groups" ? sharing.group_ids : [],
        copied_from: kind === "copy" ? (report?.key ?? null) : kind === "update" ? (report?.copied_from ?? null) : start.copiedFrom,
      };
      if (kind === "update" && report?.id != null) {
        return reportsApi.update(report.id, { ...body, version: report.version ?? 1 });
      }
      return reportsApi.create(body);
    },
    onSuccess: (saved) => {
      setDone(true);
      void queryClient.invalidateQueries({ queryKey: ["reports"] });
      void navigate({ to: "/agent/reports/$reportKey", params: { reportKey: saved.key } });
    },
    onError: (err) => {
      const conflict = err instanceof ApiError && err.status === 409;
      setSaveError({ message: reportErrorMessage(err, conflict ? t("reports.saved.conflict") : t("reports.error")), conflict });
    },
  });

  const openSave = (kind: "create" | "update" | "copy") => {
    setTried(false);
    setSaveError(null);
    if (kind === "copy" && report && state.name === (report.name ?? "")) {
      dispatch({ type: "setMeta", name: t("reports.builder.copyOf", { name: state.name }) });
    }
    if (kind === "copy") setCopyShare(canShare ? share : PRIVATE_SHARE);
    setDialog(kind);
  };

  const submit = () => {
    if (!dialog) return;
    if (saveIssues.length > 0 || !definition) {
      setTried(true);
      return;
    }
    setSaveError(null);
    save.mutate(dialog);
  };

  const loadFile = async (file: File) => {
    try {
      const out = await reportsApi.importFile(await file.text());
      dispatch({ type: "load", state: fromDefinition(out.definition, { name: out.name, description: out.description ?? "" }) });
      setFileError(null);
    } catch (err) {
      setFileError(reportErrorMessage(err, t("reports.builder.loadFileError")));
    }
  };

  const leave = () => {
    if (mode === "edit" && report) void navigate({ to: "/agent/reports/$reportKey", params: { reportKey: report.key } });
    else void navigate({ to: "/agent/reports" });
  };

  const title = mode === "edit" ? t("reports.builder.editTitle") : t("reports.builder.newTitle");
  const canSave = definition !== null;

  return (
    <div className="mx-auto w-full max-w-[96rem] space-y-4 px-4 py-6" data-testid="report-builder-page">
      <div className="space-y-3">
        <Link
          to="/agent/reports"
          className="inline-flex items-center gap-1 text-xs text-muted hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
        >
          <span aria-hidden>←</span> {t("reports.back")}
        </Link>
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-0 flex-1 basis-72">
            <h1 className="font-display text-2xl font-semibold tracking-tight text-ink">{title}</h1>
            <label htmlFor="builder-name" className="sr-only">
              {t("reports.builder.name")}
            </label>
            <input
              id="builder-name"
              className={`${inputCls} mt-2 w-full max-w-xl`}
              value={state.name}
              maxLength={200}
              placeholder={t("reports.builder.namePlaceholder")}
              onChange={(e) => dispatch({ type: "setMeta", name: e.target.value })}
              data-testid="builder-name"
            />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {dirty && (
              <span className="text-xs text-muted" data-testid="builder-unsaved">
                {t("reports.builder.unsaved")}
              </span>
            )}
            <input
              ref={fileInput}
              type="file"
              accept=".json,.yaml,.yml,application/json,text/yaml"
              className="sr-only"
              tabIndex={-1}
              aria-label={t("reports.builder.loadFile")}
              data-testid="builder-file-input"
              onChange={(e) => {
                const file = e.target.files?.[0];
                e.target.value = "";
                if (file) void loadFile(file);
              }}
            />
            <Button onClick={() => fileInput.current?.click()} data-testid="builder-load-file">
              {t("reports.builder.loadFile")}
            </Button>
            {mode === "edit" && (
              <Button onClick={() => openSave("copy")} disabled={!canSave} data-testid="builder-save-copy">
                {t("reports.builder.saveCopy")}
              </Button>
            )}
            <Button
              onClick={() => (dirty ? setLeaving(true) : leave())}
              data-testid="builder-cancel"
            >
              {t("reports.builder.cancel")}
            </Button>
            <Button
              variant="primary"
              onClick={() => openSave(mode === "edit" ? "update" : "create")}
              disabled={!canSave}
              data-testid="builder-save"
            >
              {t("reports.builder.save")}
            </Button>
          </div>
        </div>
        {fileError && (
          <p className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-ink" role="alert" data-testid="builder-file-error">
            {fileError}
          </p>
        )}
      </div>

      <div className="grid gap-6 lg:grid-cols-[20rem_minmax(0,1fr)_16rem] xl:grid-cols-[22rem_minmax(0,1fr)_18rem]">
        <BuilderFieldsPanel state={state} dispatch={dispatch} sources={sources} source={source} issues={issues} />
        <BuilderPreview definition={definition} issues={issues} />
        <BuilderOptionsPanel state={state} dispatch={dispatch} source={source} />
      </div>

      <SaveReportDialog
        open={dialog !== null}
        onClose={() => setDialog(null)}
        mode={dialog ?? "create"}
        name={state.name}
        description={state.description}
        onMeta={(patch) => dispatch({ type: "setMeta", ...patch })}
        share={activeShare}
        onShare={dialog === "copy" ? setCopyShare : setShare}
        canShare={canShare}
        pending={save.isPending}
        error={saveError}
        issues={tried ? saveIssues : []}
        onSubmit={submit}
        onReload={onReload}
      />
      <ConfirmDialog
        open={leaving}
        title={t("reports.builder.discardTitle")}
        message={t("reports.builder.discardConfirm")}
        confirmLabel={t("reports.builder.discardLeave")}
        cancelLabel={t("reports.builder.discardKeep")}
        variant="danger"
        onCancel={() => setLeaving(false)}
        onConfirm={() => {
          setLeaving(false);
          setDone(true);
          leave();
        }}
      />
    </div>
  );
}

/** `/agent/reports/new` (optionally `?from=<report key>`) and `/agent/reports/$reportKey/edit`: the report builder. */
export function ReportBuilderPage() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const navigate = useNavigate();
  const { reportKey } = useParams({ strict: false }) as { reportKey?: string };
  const { from } = useSearch({ strict: false }) as ReportBuilderSearch;
  const mode: Mode = reportKey ? "edit" : "new";
  const baseKey = reportKey ?? from;
  const builtinEdit = mode === "edit" && reportKey !== undefined && !isSavedKey(reportKey);
  const allowed = user?.can_create_reports === true;

  useEffect(() => {
    if (allowed && builtinEdit) void navigate({ to: "/agent/reports/new", search: { from: reportKey }, replace: true });
  }, [allowed, builtinEdit, navigate, reportKey]);

  const [nonce, setNonce] = useState(0);
  const reportQ = useQuery({
    queryKey: ["reports", "get", baseKey, "builder"],
    queryFn: ({ signal }) => reportsApi.get(baseKey as string, signal),
    enabled: allowed && baseKey !== undefined && !builtinEdit,
    retry: false,
    // The editor owns its copy: a refetch on focus must not replace what is being edited.
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    gcTime: 0,
  });
  const sourcesQ = useQuery({
    queryKey: ["reports", "sources"],
    queryFn: ({ signal }) => reportsApi.sources(signal),
    staleTime: 5 * 60_000,
    enabled: allowed,
  });
  const report = reportQ.data;
  // A copy of a plugin report starts from its translated name: wait for the texts.
  // Also the labels of every source (fields panel, preview): a plugin source must not show raw keys.
  const pluginTexts = usePluginReportTexts(
    useMemo(() => [report], [report]),
    useMemo(() => labelKeysOf(sourcesQ.data), [sourcesQ.data]),
  );
  const [textsSeen, setTextsSeen] = useState(false);
  // Not while the report is still pending: `loading` is false then only because no plugin key is known yet.
  // A failed load settles too, so the load error shows instead of an endless spinner.
  const baseSettled = baseKey === undefined || !reportQ.isPending;
  useEffect(() => {
    if (!pluginTexts.loading && baseSettled) setTextsSeen(true);
  }, [pluginTexts.loading, baseSettled]);
  // Sticky: a later language change must not unmount the editor.
  const textsReady = textsSeen || (!pluginTexts.loading && baseSettled);
  const start = useMemo(
    () => (baseKey === undefined || report ? startFrom(mode, report, t) : null),
    // the start is taken once per loaded report; a language change must not reset the editor
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [mode, report, textsReady],
  );

  if (!allowed) {
    return (
      <Notice testId="builder-no-right">
        <p className="text-sm text-ink">{t("reports.builder.noRight")}</p>
      </Notice>
    );
  }
  if (builtinEdit || (baseKey !== undefined && reportQ.isPending) || sourcesQ.isPending || !textsReady) {
    return (
      <div className="flex justify-center py-16">
        <Spinner />
      </div>
    );
  }
  if (baseKey !== undefined && (!report || !start)) {
    return (
      <Notice testId="builder-load-error">
        <p className="text-sm text-ink" role="alert">
          {report?.definition_error ?? reportErrorMessage(reportQ.error, t("reports.builder.loadError"))}
        </p>
      </Notice>
    );
  }
  if (mode === "edit" && report?.read_only) {
    return (
      <Notice testId="builder-read-only">
        <p className="text-sm text-ink">{t("reports.builder.readOnly")}</p>
        <Link
          to="/agent/reports/new"
          search={{ from: report.key }}
          className="mt-4 inline-flex rounded-md border border-hairline bg-surface px-3 py-1.5 text-sm font-medium text-ink hover:bg-surface-subtle"
          data-testid="builder-edit-copy"
        >
          {t("reports.builder.editCopy")}
        </Link>
      </Notice>
    );
  }
  return (
    <Editor
      key={`${baseKey ?? "blank"}:${nonce}`}
      mode={mode}
      start={start as Start}
      report={mode === "edit" ? report : undefined}
      onReload={() => {
        void reportQ.refetch().then(() => setNonce((n) => n + 1));
      }}
    />
  );
}
