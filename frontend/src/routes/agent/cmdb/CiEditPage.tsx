import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useBlocker, useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { CiAttributeForm } from "@/components/cmdb/CiAttributeForm";
import { CmdbModeNotice } from "@/components/cmdb/CmdbModeNotice";
import {
  emptyValues,
  pruneEmpty,
  validateValues,
  VALIDATION_CODES,
  type AttrValues,
} from "@/components/cmdb/attributeModel";
import { DEPLOYMENT_CATALOG, INCIDENT_CATALOG } from "@/components/cmdb/ciFilterModel";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { SelectField } from "@/components/ui/SelectField";
import { Spinner } from "@/components/ui/Spinner";
import { ApiError } from "@/lib/api";
import { apiErrorMessage } from "@/lib/avatarApi";
import {
  cmdbApi,
  useCmdbStatus,
  type AttributeDef,
  type CatalogItem,
  type Ci,
  type CmdbAttachment,
  type CmdbClass,
  type FormOptions,
} from "@/lib/cmdbApi";

const INPUT_CLS =
  "w-full rounded border border-hairline bg-surface px-2 py-1.5 text-sm text-ink focus:outline-none focus:ring-1 focus:ring-accent";
const LABEL_CLS = "mb-1 block text-xs font-medium text-muted";

type ErrorBody = { code?: string; keys?: string[]; issues?: Array<{ path: string; code: string; message?: string }> };

/** The `{code, …}` object of a CMDB error; FastAPI wraps it once in `detail`. */
function errorBody(err: unknown): ErrorBody {
  if (!(err instanceof ApiError)) return {};
  const d = err.detail as { detail?: unknown } | null;
  const inner = d && typeof d === "object" && "detail" in d ? d.detail : d;
  return inner && typeof inner === "object" ? (inner as ErrorBody) : {};
}

/** Values of a loaded CI, limited to the keys the current definition knows (unknown keys stay server side). */
function valuesOf(defs: AttributeDef[], raw: Record<string, unknown> | undefined): AttrValues {
  const base = emptyValues(defs);
  for (const d of defs) {
    const entries = raw?.[d.key];
    if (Array.isArray(entries) && entries.length > 0) base[d.key] = entries as AttrValues[string];
  }
  return base;
}

function pickState(items: CatalogItem[], preferredKey: string): number {
  const valid = items.filter((i) => i.valid);
  return (valid.find((i) => i.item_key === preferredKey) ?? valid[0] ?? items[0])?.id ?? 0;
}

type EditorProps = {
  mode: "create" | "edit";
  cls: CmdbClass;
  defs: AttributeDef[];
  options: FormOptions;
  deploymentItems: CatalogItem[];
  incidentItems: CatalogItem[];
  ci: Ci | null;
  initialAttachments: CmdbAttachment[];
  onReload: () => void;
};

function CiEditor({ mode, cls, defs, options, deploymentItems, incidentItems, ci, initialAttachments, onReload }: EditorProps) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const navigate = useNavigate();

  const initial = useMemo(
    () => ({
      name: ci?.name ?? "",
      deployment: ci?.deployment_state_id ?? pickState(deploymentItems, "production"),
      incident: ci?.incident_state_id ?? pickState(incidentItems, "operational"),
      note: "",
      values: valuesOf(defs, ci?.current_version?.attributes as Record<string, unknown> | undefined),
    }),
    // The editor is remounted (key) whenever the data is reloaded.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );
  const [name, setName] = useState(initial.name);
  const [deployment, setDeployment] = useState(initial.deployment);
  const [incident, setIncident] = useState(initial.incident);
  const [note, setNote] = useState(initial.note);
  const [values, setValues] = useState<AttrValues>(initial.values);
  const [baseline, setBaseline] = useState(() => JSON.stringify(initial));
  const [attachments, setAttachments] = useState<CmdbAttachment[]>(initialAttachments);
  const [staged, setStaged] = useState<number[]>([]);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [nameError, setNameError] = useState(false);
  const [issueCount, setIssueCount] = useState(0);
  const [serverUnknown, setServerUnknown] = useState<string[]>([]);
  const [dropUnknown, setDropUnknown] = useState(false);
  const [needConfirm, setNeedConfirm] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [message, setMessage] = useState<{ kind: "error" | "info"; text: string } | null>(null);
  const allowLeave = useRef(false);

  const unknown = useMemo(() => [...new Set([...(ci?.unknown_keys ?? []), ...serverUnknown])].sort(), [ci, serverUnknown]);
  const current = JSON.stringify({ name, deployment, incident, note, values });
  const dirty = current !== baseline;
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;

  const blocker = useBlocker({
    shouldBlockFn: () => dirtyRef.current && !allowLeave.current,
    enableBeforeUnload: () => dirtyRef.current && !allowLeave.current,
    withResolver: true,
  });

  const goDetail = (id: number) => {
    allowLeave.current = true;
    void navigate({ to: "/agent/cmdb/$ciId", params: { ciId: String(id) } });
  };

  const saveM = useMutation({
    mutationFn: async () => {
      const attributes = pruneEmpty(defs, values);
      if (mode === "create") {
        const created = await cmdbApi.create({
          class_id: cls.id,
          name: name.trim(),
          deployment_state_id: deployment,
          incident_state_id: incident,
          attributes,
          change_note: note.trim() || null,
          attachment_ids: staged,
        });
        return { ci: created, changed: true };
      }
      return cmdbApi.update(ci!.id, {
        expected_lock_version: ci!.lock_version,
        name: name.trim(),
        deployment_state_id: deployment,
        incident_state_id: incident,
        attributes,
        change_note: note.trim() || null,
        drop_unknown: dropUnknown,
      });
    },
    onSuccess: async (out) => {
      await qc.invalidateQueries({ queryKey: ["cmdb", "cis"] });
      qc.removeQueries({ queryKey: ["cmdb", "ci", out.ci.id] });
      if (out.changed) {
        goDetail(out.ci.id);
      } else {
        setBaseline(current);
        setMessage({ kind: "info", text: t("cmdb.edit.unchanged") });
      }
    },
    onError: (err) => {
      const body = errorBody(err);
      if (err instanceof ApiError && err.status === 409 && body.code === "conflict") {
        setConflict(true);
        return;
      }
      if (err instanceof ApiError && err.status === 422 && body.code === "invalid_values") {
        const issues = body.issues ?? [];
        setErrors(
          Object.fromEntries(
            issues.map((i) => [i.path, (VALIDATION_CODES as readonly string[]).includes(i.code) ? `cmdb.validation.${i.code}` : (i.message ?? i.code)]),
          ),
        );
        setIssueCount(issues.length);
        return;
      }
      if (err instanceof ApiError && err.status === 422 && body.code === "unknown_attributes") {
        setServerUnknown(body.keys ?? []);
        setNeedConfirm(true);
        return;
      }
      setMessage({ kind: "error", text: apiErrorMessage(err, t("cmdb.edit.saveFailed")) });
    },
  });

  const save = () => {
    setMessage(null);
    setNeedConfirm(false);
    const found = validateValues(defs, values);
    const noName = name.trim() === "";
    setErrors(found);
    setNameError(noName);
    setIssueCount(Object.keys(found).length + (noName ? 1 : 0));
    if (noName || Object.keys(found).length > 0) return;
    if (unknown.length > 0 && !dropUnknown) {
      setNeedConfirm(true);
      return;
    }
    saveM.mutate();
  };

  const upload = async (file: File): Promise<CmdbAttachment | void> => {
    try {
      const made = await cmdbApi.uploadAttachment(file, mode === "edit" ? ci!.id : null);
      setAttachments((prev) => [...prev, made]);
      if (mode === "create") setStaged((prev) => [...prev, made.id]);
      return made;
    } catch (err) {
      setMessage({ kind: "error", text: apiErrorMessage(err, t("cmdb.edit.uploadFailed")) });
    }
  };

  const stateItems = (items: CatalogItem[]) => items.filter((i) => i.valid || i.id === deployment || i.id === incident).map((i) => ({ value: i.id, label: i.name }));

  return (
    <form
      className="mx-auto max-w-4xl space-y-5 p-4 sm:p-6"
      data-testid="ci-edit-page"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        save();
      }}
    >
      <div className="space-y-1">
        <Link to={mode === "edit" ? "/agent/cmdb/$ciId" : "/agent/cmdb"} params={mode === "edit" ? { ciId: String(ci!.id) } : ({} as never)} className="text-sm text-accent hover:underline" data-testid="ci-edit-back">
          {t(mode === "edit" ? "cmdb.edit.backToCi" : "cmdb.detail.back")}
        </Link>
        <h1 className="font-display text-xl font-bold text-ink" data-testid="ci-edit-title">
          {mode === "edit" ? t("cmdb.edit.titleEdit", { number: ci!.number }) : t("cmdb.edit.titleNew", { class: cls.name })}
        </h1>
      </div>

      {issueCount > 0 && (
        <p role="alert" className="rounded-md border border-danger px-3 py-2 text-sm text-danger" data-testid="ci-edit-summary">
          {t("cmdb.edit.summary", { count: issueCount })}
        </p>
      )}
      {message && (
        <p
          role={message.kind === "error" ? "alert" : "status"}
          className={message.kind === "error" ? "text-sm text-danger" : "rounded-md border border-hairline bg-surface-subtle px-3 py-2 text-sm text-ink"}
          data-testid={message.kind === "error" ? "ci-edit-error" : "ci-edit-info"}
        >
          {message.text}
        </p>
      )}

      {unknown.length > 0 && (
        <div role="note" className="space-y-2 rounded-md border border-warn px-3 py-3 text-sm text-ink" data-testid="ci-edit-unknown">
          <p>{t("cmdb.unknownValues")}</p>
          <p className="text-xs text-muted">{t("cmdb.edit.unknownKeys", { keys: unknown.join(", ") })}</p>
          <label className="inline-flex items-center gap-2">
            <input type="checkbox" checked={dropUnknown} onChange={(e) => setDropUnknown(e.target.checked)} data-testid="ci-edit-drop-unknown" />
            {t("cmdb.edit.dropUnknown")}
          </label>
          {needConfirm && (
            <p role="alert" className="text-danger" data-testid="ci-edit-unknown-confirm">
              {t("cmdb.edit.unknownConfirm")}
            </p>
          )}
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <label htmlFor="ci-edit-name" className={LABEL_CLS}>
            {t("cmdb.edit.name")}
            <span className="ml-0.5 text-danger" aria-hidden="true">
              *
            </span>
          </label>
          <input
            id="ci-edit-name"
            className={INPUT_CLS}
            value={name}
            maxLength={250}
            onChange={(e) => setName(e.target.value)}
            aria-invalid={nameError}
            aria-describedby={nameError ? "ci-edit-name-error" : undefined}
            data-testid="ci-edit-name"
          />
          {nameError && (
            <p id="ci-edit-name-error" role="alert" className="mt-1 text-xs text-danger">
              {t("cmdb.validation.required")}
            </p>
          )}
        </div>
        <div>
          <span id="ci-edit-deployment-label" className={LABEL_CLS}>
            {t("cmdb.detail.deployment")}
          </span>
          <SelectField items={stateItems(deploymentItems)} value={deployment} onChange={setDeployment} testId="ci-edit-deployment" aria-label={t("cmdb.detail.deployment")} />
        </div>
        <div>
          <span id="ci-edit-incident-label" className={LABEL_CLS}>
            {t("cmdb.detail.incidentOwn")}
          </span>
          <SelectField items={stateItems(incidentItems)} value={incident} onChange={setIncident} testId="ci-edit-incident" aria-label={t("cmdb.detail.incidentOwn")} />
        </div>
      </div>

      <section className="space-y-2" aria-labelledby="ci-edit-attrs">
        <h2 id="ci-edit-attrs" className="text-sm font-semibold text-ink">
          {t("cmdb.edit.attributes")}
        </h2>
        <CiAttributeForm defs={defs} values={values} onChange={setValues} options={options} errors={errors} disabled={saveM.isPending} attachments={attachments} onUpload={upload} />
      </section>

      <div>
        <label htmlFor="ci-edit-note" className={LABEL_CLS}>
          {t("cmdb.edit.changeNote")}
        </label>
        <input id="ci-edit-note" className={INPUT_CLS} value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} data-testid="ci-edit-note" />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" variant="primary" disabled={saveM.isPending} data-testid="ci-edit-save">
          {t("common.save")}
        </Button>
        <Button type="button" variant="secondary" onClick={() => void navigate({ to: mode === "edit" ? "/agent/cmdb/$ciId" : "/agent/cmdb", params: mode === "edit" ? { ciId: String(ci!.id) } : ({} as never) } as never)} data-testid="ci-edit-cancel">
          {t("common.cancel")}
        </Button>
      </div>

      <ConfirmDialog
        open={conflict}
        title={t("cmdb.edit.conflict.title")}
        message={t("cmdb.edit.conflict.message")}
        confirmLabel={t("cmdb.edit.conflict.reload")}
        role="alertdialog"
        onConfirm={() => {
          setConflict(false);
          allowLeave.current = true;
          onReload();
        }}
        onCancel={() => setConflict(false)}
      />
      <ConfirmDialog
        open={blocker.status === "blocked"}
        title={t("cmdb.edit.leave.title")}
        message={t("cmdb.edit.leave.message")}
        confirmLabel={t("cmdb.edit.leave.confirm")}
        cancelLabel={t("cmdb.edit.leave.stay")}
        variant="danger"
        role="alertdialog"
        onConfirm={() => blocker.proceed?.()}
        onCancel={() => blocker.reset?.()}
      />
    </form>
  );
}

/**
 * Create (`classId` given) or edit (`ciId` given) a CI: name, states, nested attributes with
 * attachments and a change note. 422 issues land under their inputs, unknown values need an explicit
 * "Werte entfernen", a stale save opens the conflict dialog and unsaved changes block navigation.
 */
export function CiEditPage({ ciId, classId }: { ciId?: number; classId?: number }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const edit = ciId !== undefined;
  const statusQ = useCmdbStatus();
  const active = statusQ.data?.mode === "tiqora";
  const [reloadKey, setReloadKey] = useState(0);

  const classesQ = useQuery({ queryKey: ["cmdb", "classes"], queryFn: () => cmdbApi.classes(), enabled: active, staleTime: 60_000 });
  const ciQ = useQuery({
    queryKey: ["cmdb", "ci", ciId, "edit", reloadKey],
    queryFn: () => cmdbApi.get(ciId!),
    enabled: active && edit,
    gcTime: 0,
    retry: false,
  });
  const ci = ciQ.data ?? null;
  const effectiveClassId = edit ? ci?.class.id : classId;
  const cls = classesQ.data?.find((c) => c.id === effectiveClassId) ?? null;
  const defQ = useQuery({
    queryKey: ["cmdb", "class-definition", cls?.id, cls?.current_definition_version],
    queryFn: () => cmdbApi.definition(cls!.id, cls!.current_definition_version!),
    enabled: active && !!cls && !cls.definition && cls.current_definition_version != null,
  });
  const definition = cls?.definition ?? defQ.data ?? null;
  const optionsQ = useQuery({ queryKey: ["cmdb", "form-options", cls?.id], queryFn: () => cmdbApi.formOptions(cls!.id), enabled: active && !!cls });
  const deploymentQ = useQuery({ queryKey: ["cmdb", "catalog", DEPLOYMENT_CATALOG], queryFn: () => cmdbApi.catalog(DEPLOYMENT_CATALOG), enabled: active, staleTime: 60_000 });
  const incidentQ = useQuery({ queryKey: ["cmdb", "catalog", INCIDENT_CATALOG], queryFn: () => cmdbApi.catalog(INCIDENT_CATALOG), enabled: active, staleTime: 60_000 });
  const attachmentsQ = useQuery({
    queryKey: ["cmdb", "ci", ciId, "attachments", reloadKey],
    queryFn: () => cmdbApi.ciAttachments(ciId!),
    enabled: active && edit,
    gcTime: 0,
  });

  const shell = (children: React.ReactNode) => (
    <div className="mx-auto max-w-4xl space-y-3 p-4 sm:p-6" data-testid="ci-edit-shell">
      {children}
    </div>
  );

  if (statusQ.data && !active) return shell(<CmdbModeNotice status={statusQ.data} />);

  if (edit && ciQ.error instanceof ApiError && ciQ.error.status === 404) {
    return shell(
      <>
        <p role="alert" className="text-sm text-ink" data-testid="ci-edit-not-found">
          {t("cmdb.detail.notFound")}
        </p>
        <Link to="/agent/cmdb" className="text-sm text-accent hover:underline">
          {t("cmdb.detail.back")}
        </Link>
      </>,
    );
  }
  if (edit && ci && !ci.can_write) {
    return shell(
      <>
        <p role="alert" className="text-sm text-ink" data-testid="ci-edit-forbidden">
          {t("cmdb.edit.forbidden")}
        </p>
        <Link to="/agent/cmdb/$ciId" params={{ ciId: String(ci.id) }} className="text-sm text-accent hover:underline">
          {t("cmdb.edit.backToCi")}
        </Link>
      </>,
    );
  }

  if (!edit && classId === undefined) {
    if (classesQ.isPending) return shell(<Spinner />);
    const writable = (classesQ.data ?? []).filter((c) => c.valid && c.can_write);
    return shell(
      <div className="space-y-3" data-testid="ci-edit-pick-class">
        <h1 className="font-display text-xl font-bold text-ink">{t("cmdb.edit.pickClass")}</h1>
        {writable.length === 0 ? (
          <p className="text-sm text-muted" data-testid="ci-edit-no-classes">
            {t("cmdb.edit.noWritableClasses")}
          </p>
        ) : (
          <>
            <p className="text-sm text-muted">{t("cmdb.edit.pickClassHint")}</p>
            <ul className="grid gap-2 sm:grid-cols-2">
              {writable.map((c) => (
                <li key={c.id}>
                  <Button
                    type="button"
                    variant="secondary"
                    className="w-full justify-start"
                    data-testid={`ci-edit-class-${c.id}`}
                    onClick={() => void navigate({ to: "/agent/cmdb/new", search: { class: c.id } })}
                  >
                    {c.name}
                  </Button>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>,
    );
  }

  const failed = ciQ.isError || classesQ.isError || defQ.isError || optionsQ.isError || deploymentQ.isError || incidentQ.isError || attachmentsQ.isError;
  if (failed) {
    return shell(
      <p role="alert" className="text-sm text-danger" data-testid="ci-edit-load-failed">
        {t("cmdb.edit.loadFailed")}
      </p>,
    );
  }
  if (!classesQ.isSuccess || (!cls && !classesQ.isPending)) {
    return classesQ.isSuccess && (edit ? ci : true)
      ? shell(
          <p role="alert" className="text-sm text-ink" data-testid="ci-edit-not-found">
            {t("cmdb.detail.notFound")}
          </p>,
        )
      : shell(<Spinner />);
  }
  if (!cls || !definition || !optionsQ.data || !deploymentQ.data || !incidentQ.data || (edit && (!ci || !attachmentsQ.data))) return shell(<Spinner />);
  if (!edit && !cls.can_write) {
    return shell(
      <p role="alert" className="text-sm text-ink" data-testid="ci-edit-forbidden">
        {t("cmdb.edit.forbiddenClass")}
      </p>,
    );
  }

  return (
    <CiEditor
      key={`${cls.id}-${ciId ?? "new"}-${reloadKey}-${ci?.lock_version ?? 0}`}
      mode={edit ? "edit" : "create"}
      cls={cls}
      defs={definition.attributes}
      options={optionsQ.data}
      deploymentItems={deploymentQ.data}
      incidentItems={incidentQ.data}
      ci={ci}
      initialAttachments={attachmentsQ.data ?? []}
      onReload={() => setReloadKey((k) => k + 1)}
    />
  );
}

/** Route component of `/agent/cmdb/$ciId/edit`. */
export function CiEditRoute() {
  const { ciId } = useParams({ from: "/agent/cmdb/$ciId/edit" });
  const id = Number(ciId);
  return <CiEditPage ciId={Number.isInteger(id) && id > 0 ? id : -1} />;
}

/** Route component of `/agent/cmdb/new` (`?class=`). */
export function CiNewRoute() {
  const search = useSearch({ from: "/agent/cmdb/new" }) as { class?: number };
  return <CiEditPage classId={search.class} />;
}
