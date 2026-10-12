import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useBlocker, useParams } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { CmdbModeNotice } from "@/components/cmdb/CmdbModeNotice";
import { DefinitionEditor } from "@/components/cmdb/DefinitionEditor";
import { DefinitionImportDialog } from "@/components/cmdb/DefinitionImportDialog";
import {
  fromDefinition,
  localIssues,
  toDefinition,
  uidForPath,
  type DraftAttr,
} from "@/components/cmdb/definitionModel";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Dialog } from "@/components/ui/Dialog";
import { SelectField } from "@/components/ui/SelectField";
import { Spinner } from "@/components/ui/Spinner";
import { Switch } from "@/components/ui/Switch";
import { api } from "@/lib/api";
import { apiErrorMessage } from "@/lib/avatarApi";
import type { AttributeDef, CmdbDefinition } from "@/lib/cmdbApi";
import { cmdbAdminApi, type DefinitionChange, type DefinitionIssue } from "@/lib/cmdbAdminApi";
import { formatDateTime } from "@/lib/format";

const INPUT = "w-full rounded border border-hairline bg-surface px-2 py-1.5 text-sm text-ink focus:outline-none focus:ring-1 focus:ring-accent disabled:opacity-60";
const LABEL = "mb-1 block text-xs font-medium uppercase tracking-wide text-muted";

type ClassForm = { name: string; icon: string; prefix: string; groupId: number | null; portal: boolean; valid: boolean };

function DiffList({ classId, version }: { classId: number; version: number }) {
  const { t } = useTranslation();
  const q = useQuery({
    queryKey: ["admin", "cmdb", "class", classId, "diff", version],
    queryFn: () => cmdbAdminApi.definitionDiff(classId, version - 1, version),
  });
  if (q.isLoading) return <Spinner />;
  if (q.isError) return <p role="alert" className="text-sm text-danger">{apiErrorMessage(q.error, t("admin.cmdb.editor.versions.diffError"))}</p>;
  const changes: DefinitionChange[] = q.data ?? [];
  if (changes.length === 0) return <p className="text-sm text-muted" data-testid={`def-diff-${version}`}>{t("admin.cmdb.editor.versions.noChanges")}</p>;
  return (
    <ul className="space-y-0.5 text-sm" data-testid={`def-diff-${version}`}>
      {changes.map((c, i) => (
        <li key={i} className="flex flex-wrap gap-x-2">
          <span className="rounded bg-surface-subtle px-1.5 text-xs text-muted">{t(`admin.cmdb.editor.diff.${c.kind}`)}</span>
          <span className="font-mono text-xs text-ink">{c.path}</span>
          {c.detail && <span className="text-xs text-muted">{c.detail}</span>}
        </li>
      ))}
    </ul>
  );
}

/** Admin editor of one CMDB class: its fields and the attribute definition (nested tree, versions, Znuny import). */
export function CmdbClassEditorPage() {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const { classId: classIdParam } = useParams({ strict: false }) as { classId?: string };
  const classId = Number(classIdParam);

  const classQ = useQuery({ queryKey: ["admin", "cmdb", "class", classId], queryFn: () => cmdbAdminApi.getClass(classId), enabled: Number.isInteger(classId) && classId > 0 });
  const defsQ = useQuery({ queryKey: ["admin", "cmdb", "class", classId, "definitions"], queryFn: () => cmdbAdminApi.definitions(classId), enabled: classQ.isSuccess });
  const catalogClassesQ = useQuery({ queryKey: ["admin", "cmdb", "catalog-classes"], queryFn: () => cmdbAdminApi.catalogClasses() });
  const classesQ = useQuery({ queryKey: ["admin", "cmdb", "classes"], queryFn: () => cmdbAdminApi.classes() });
  const statusQ = useQuery({ queryKey: ["admin", "cmdb", "status"], queryFn: () => cmdbAdminApi.status() });
  const groupsQ = useQuery({
    queryKey: ["admin", "groups", "ref"],
    queryFn: ({ signal }) => api.adminGroups.list({ valid: "valid", pageSize: 500 }, signal),
  });
  const groupItems = (groupsQ.data?.items ?? []).map((g) => ({ value: g.id, label: g.name }));

  const cls = classQ.data;
  const [form, setForm] = useState<ClassForm | null>(null);
  const [formBase, setFormBase] = useState("");
  const [tree, setTree] = useState<DraftAttr[]>([]);
  const [treeBase, setTreeBase] = useState("[]");
  const [selected, setSelected] = useState<string | null>(null);
  const [serverIssues, setServerIssues] = useState<Array<{ uid: string | null; issue: DefinitionIssue }> | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [versionOpen, setVersionOpen] = useState(false);
  const [comment, setComment] = useState("");
  const [diffFor, setDiffFor] = useState<number | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Reset the class form whenever the stored class changes (first load, after a save).
  const classKey = cls ? `${cls.id}:${cls.lock_version}` : null;
  useEffect(() => {
    if (!cls) return;
    const f: ClassForm = { name: cls.name, icon: cls.icon ?? "", prefix: cls.number_prefix, groupId: cls.group_id, portal: cls.portal_visible, valid: cls.valid };
    setForm(f);
    setFormBase(JSON.stringify(f));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [classKey]);
  // Reset the tree when the current definition version changes (first load, after saving a version or loading one).
  const defKey = cls ? `${cls.id}:${cls.current_definition_version ?? 0}` : null;
  useEffect(() => {
    if (!cls) return;
    const next = fromDefinition((cls.definition?.attributes ?? []) as AttributeDef[]);
    setTree(next);
    setTreeBase(JSON.stringify(toDefinition(next)));
    setSelected(null);
    setServerIssues(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [defKey]);

  const treeDirty = JSON.stringify(toDefinition(tree)) !== treeBase;
  const formDirty = form !== null && JSON.stringify(form) !== formBase;
  const dirty = treeDirty || formDirty;
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  const blocker = useBlocker({
    shouldBlockFn: () => dirtyRef.current,
    enableBeforeUnload: () => dirtyRef.current,
    withResolver: true,
  });

  const local = useMemo(() => localIssues(tree), [tree]);
  const issueText = useMemo(() => {
    const out: Record<string, string> = {};
    for (const [uid, code] of Object.entries(local)) out[uid] = t(`admin.cmdb.editor.issue.${code}`);
    for (const s of serverIssues ?? []) if (s.uid) out[s.uid] = s.issue.message;
    return out;
  }, [local, serverIssues, t]);
  const localCount = Object.keys(local).length;

  const changeTree = (next: DraftAttr[]) => {
    setTree(next);
    setServerIssues(null);
    setNotice(null);
  };

  const saveClassM = useMutation({
    mutationFn: () => {
      const f = form as ClassForm;
      return cmdbAdminApi.updateClass(classId, {
        class_key: (cls as NonNullable<typeof cls>).class_key,
        name: f.name.trim(),
        icon: f.icon.trim() || null,
        number_prefix: f.prefix.trim() || null,
        group_id: f.groupId as number,
        portal_visible: f.portal,
        valid: f.valid,
        position: (cls as NonNullable<typeof cls>).position,
        expected_lock_version: (cls as NonNullable<typeof cls>).lock_version,
      });
    },
    onSuccess: (saved) => {
      qc.setQueryData(["admin", "cmdb", "class", classId], saved);
      void qc.invalidateQueries({ queryKey: ["admin", "cmdb", "classes"] });
      setNotice(t("admin.cmdb.editor.classSaved"));
    },
  });

  const validateM = useMutation({
    mutationFn: () => cmdbAdminApi.validateDefinition({ attributes: toDefinition(tree) as never, class_id: classId }),
    onSuccess: (issues) => {
      setServerIssues(issues.map((issue) => ({ uid: uidForPath(tree, issue.path), issue })));
      setNotice(null);
    },
  });

  const saveVersionM = useMutation({
    mutationFn: () => cmdbAdminApi.addDefinition(classId, { attributes: toDefinition(tree) as never, comment: comment.trim() || null }),
    onSuccess: async () => {
      setVersionOpen(false);
      setComment("");
      setNotice(t("admin.cmdb.editor.versions.saved"));
      await qc.invalidateQueries({ queryKey: ["admin", "cmdb", "class", classId] });
    },
  });

  const jumpTo = (uid: string | null) => {
    if (!uid) return;
    setSelected(uid);
    requestAnimationFrame(() => document.querySelector(`[data-testid="def-row-${uid}"]`)?.scrollIntoView?.({ block: "nearest" }));
  };

  if (classQ.isLoading || !cls || !form) {
    return (
      <div className="mx-auto max-w-5xl p-4 sm:p-6" data-testid="admin-cmdb-class-editor">
        {classQ.isError ? (
          <p role="alert" className="text-sm text-danger" data-testid="class-load-error">{apiErrorMessage(classQ.error, t("admin.cmdb.editor.loadError"))}</p>
        ) : (
          <Spinner />
        )}
      </div>
    );
  }

  const canWrite = cls.can_write;
  const defs: CmdbDefinition[] = [...(defsQ.data ?? [])].sort((a, b) => b.version - a.version);
  const classOptions = (classesQ.data ?? []).map((c) => ({ key: c.class_key, name: c.name }));
  const setF = (p: Partial<ClassForm>) => setForm({ ...form, ...p });
  const canSaveClass = canWrite && formDirty && form.name.trim() !== "" && form.groupId !== null && !saveClassM.isPending;

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 sm:p-6" data-testid="admin-cmdb-class-editor">
      <div>
        <Link to="/admin/cmdb/classes" className="text-xs text-accent hover:underline">{t("admin.cmdb.editor.back")}</Link>
        <h1 className="mt-1 font-display text-xl font-semibold text-ink">{cls.name}</h1>
        <p className="text-xs text-muted">{t("admin.cmdb.editor.subtitle")}</p>
      </div>

      {statusQ.data && <CmdbModeNotice status={statusQ.data} />}

      <section className="space-y-3 rounded-xl border border-hairline bg-surface p-4" aria-labelledby="cls-fields">
        <h2 id="cls-fields" className="text-sm font-semibold text-ink">{t("admin.cmdb.editor.classSection")}</h2>
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label className={LABEL} htmlFor="cls-key">{t("admin.cmdb.classes.key")}</label>
            <input id="cls-key" data-testid="cls-key" className={`${INPUT} font-mono`} value={cls.class_key} readOnly disabled />
            <p className="mt-1 text-xs text-muted">{t("admin.cmdb.editor.keyReadOnly")}</p>
          </div>
          <div>
            <label className={LABEL} htmlFor="cls-name">{t("admin.cmdb.classes.name")}</label>
            <input id="cls-name" data-testid="cls-name" className={INPUT} disabled={!canWrite} maxLength={200} value={form.name} onChange={(e) => setF({ name: e.target.value })} />
          </div>
          <div>
            <label className={LABEL} htmlFor="cls-icon">{t("admin.cmdb.editor.icon")}</label>
            <input id="cls-icon" data-testid="cls-icon" className={INPUT} disabled={!canWrite} maxLength={50} value={form.icon} onChange={(e) => setF({ icon: e.target.value })} />
          </div>
          <div>
            <label className={LABEL} htmlFor="cls-prefix">{t("admin.cmdb.classes.prefix")}</label>
            <input id="cls-prefix" data-testid="cls-prefix" className={`${INPUT} font-mono`} disabled={!canWrite} maxLength={20} value={form.prefix} onChange={(e) => setF({ prefix: e.target.value })} />
          </div>
          <div>
            <label className={LABEL} htmlFor="cls-group">{t("admin.cmdb.classes.group")}</label>
            <SelectField<number> id="cls-group" testId="cls-group" items={groupItems} value={form.groupId} disabled={!canWrite} onChange={(v) => setF({ groupId: v })} placeholder={t("admin.cmdb.classes.groupPlaceholder")} />
          </div>
          <div className="flex flex-col justify-end gap-2">
            <label className="flex items-center justify-between gap-3 text-sm text-ink">
              {t("admin.cmdb.classes.portal")}
              <Switch checked={form.portal} disabled={!canWrite} onChange={(v) => setF({ portal: v })} aria-label={t("admin.cmdb.classes.portal")} testId="cls-portal" />
            </label>
            <label className="flex items-center justify-between gap-3 text-sm text-ink">
              {t("admin.cmdb.editor.valid")}
              <Switch checked={form.valid} disabled={!canWrite} onChange={(v) => setF({ valid: v })} aria-label={t("admin.cmdb.editor.valid")} testId="cls-valid" />
            </label>
          </div>
        </div>
        {saveClassM.isError && (
          <p role="alert" className="text-sm text-danger" data-testid="cls-error">{apiErrorMessage(saveClassM.error, t("admin.cmdb.classes.saveError"))}</p>
        )}
        <div className="flex justify-end">
          <Button variant="primary" disabled={!canSaveClass} onClick={() => saveClassM.mutate()} data-testid="cls-save">
            {t("admin.cmdb.editor.saveClass")}
          </Button>
        </div>
      </section>

      <section className="space-y-3" aria-labelledby="cls-def">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 id="cls-def" className="text-sm font-semibold text-ink">
              {t("admin.cmdb.editor.definition")}
              {cls.current_definition_version != null && <span className="ml-2 text-xs font-normal text-muted">{t("admin.cmdb.editor.versions.current", { version: cls.current_definition_version })}</span>}
            </h2>
            <p className="text-xs text-muted">{t("admin.cmdb.editor.definitionHelp")}</p>
          </div>
          {canWrite && (
            <div className="flex flex-wrap gap-2">
              <Button onClick={() => setImportOpen(true)} data-testid="def-import-open">{t("admin.cmdb.editor.import.open")}</Button>
              <Button disabled={validateM.isPending} onClick={() => validateM.mutate()} data-testid="def-validate">{t("admin.cmdb.editor.validate")}</Button>
              <Button variant="primary" disabled={!treeDirty || localCount > 0} onClick={() => setVersionOpen(true)} data-testid="def-save-version">
                {t("admin.cmdb.editor.saveVersion")}
              </Button>
            </div>
          )}
        </div>

        <DefinitionEditor
          tree={tree}
          onChange={changeTree}
          selectedUid={selected}
          onSelect={setSelected}
          issues={issueText}
          catalogClasses={catalogClassesQ.data ?? []}
          classOptions={classOptions}
          disabled={!canWrite}
        />

        {localCount > 0 && treeDirty && (
          <p className="text-xs text-danger" data-testid="def-local-issues">{t("admin.cmdb.editor.localIssues", { count: localCount })}</p>
        )}
        {validateM.isError && <p role="alert" className="text-sm text-danger">{apiErrorMessage(validateM.error, t("admin.cmdb.editor.validateError"))}</p>}
        {serverIssues && (
          <div className="rounded border border-hairline bg-surface p-3" data-testid="def-issues" role="status">
            {serverIssues.length === 0 ? (
              <p className="text-sm text-ink" data-testid="def-valid">{t("admin.cmdb.editor.validateOk")}</p>
            ) : (
              <>
                <p className="mb-1 text-xs font-medium uppercase tracking-wide text-muted">{t("admin.cmdb.editor.issuesTitle", { count: serverIssues.length })}</p>
                <ul className="space-y-1">
                  {serverIssues.map((s, i) => (
                    <li key={i} className="flex flex-wrap items-center gap-2 text-sm">
                      <span className="font-mono text-xs text-muted">{s.issue.path || "-"}</span>
                      <span className="text-danger">{s.issue.message}</span>
                      {s.uid && (
                        <button type="button" className="text-xs text-accent hover:underline" data-testid={`def-issue-jump-${i}`} onClick={() => jumpTo(s.uid)}>
                          {t("admin.cmdb.editor.jump")}
                        </button>
                      )}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        )}
        {notice && <p role="status" className="text-sm text-ink" data-testid="cls-notice">{notice}</p>}
      </section>

      <section className="space-y-2" aria-labelledby="cls-versions">
        <h2 id="cls-versions" className="text-sm font-semibold text-ink">{t("admin.cmdb.editor.versions.title")}</h2>
        {defs.length === 0 ? (
          <p className="text-sm text-muted">{t("admin.cmdb.editor.versions.none")}</p>
        ) : (
          <ul className="divide-y divide-hairline rounded-xl border border-hairline bg-surface" data-testid="def-versions">
            {defs.map((d) => (
              <li key={d.id} className="space-y-2 px-3 py-2" data-testid={`def-version-${d.version}`}>
                <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                  <span className="text-ink">
                    <span className="font-medium">{t("admin.cmdb.editor.versions.version", { version: d.version })}</span>
                    <span className="ml-2 text-xs text-muted">{formatDateTime(d.created_at, i18n.language)}</span>
                    {d.comment && <span className="ml-2 text-xs text-muted">{d.comment}</span>}
                  </span>
                  {d.version > 1 && (
                    <button type="button" className="text-xs text-accent hover:underline" data-testid={`def-diff-toggle-${d.version}`} onClick={() => setDiffFor(diffFor === d.version ? null : d.version)}>
                      {t("admin.cmdb.editor.versions.changes")}
                    </button>
                  )}
                </div>
                {diffFor === d.version && <DiffList classId={classId} version={d.version} />}
              </li>
            ))}
          </ul>
        )}
      </section>

      {importOpen && (
        <DefinitionImportDialog
          open
          onClose={() => setImportOpen(false)}
          onApply={(attrs) => {
            changeTree(fromDefinition(attrs));
            setSelected(null);
            setImportOpen(false);
            setNotice(t("admin.cmdb.editor.import.applied"));
          }}
        />
      )}

      <Dialog
        open={versionOpen}
        onClose={() => setVersionOpen(false)}
        title={t("admin.cmdb.editor.saveVersion")}
        description={t("admin.cmdb.editor.versions.saveHelp")}
        size="md"
        footer={
          <>
            <Button onClick={() => setVersionOpen(false)}>{t("common.cancel")}</Button>
            <Button variant="primary" disabled={saveVersionM.isPending} onClick={() => saveVersionM.mutate()} data-testid="def-version-confirm">
              {t("admin.cmdb.editor.versions.save")}
            </Button>
          </>
        }
      >
        <div className="space-y-2">
          <label className={LABEL} htmlFor="def-comment">{t("admin.cmdb.editor.versions.comment")}</label>
          <input id="def-comment" data-testid="def-comment" className={INPUT} maxLength={250} value={comment} onChange={(e) => setComment(e.target.value)} />
          {saveVersionM.isError && (
            <p role="alert" className="text-sm text-danger" data-testid="def-version-error">{apiErrorMessage(saveVersionM.error, t("admin.cmdb.editor.versions.error"))}</p>
          )}
        </div>
      </Dialog>

      <ConfirmDialog
        open={blocker.status === "blocked"}
        title={t("admin.cmdb.editor.leave.title")}
        message={t("admin.cmdb.editor.leave.message")}
        confirmLabel={t("admin.cmdb.editor.leave.confirm")}
        cancelLabel={t("admin.cmdb.editor.leave.stay")}
        variant="danger"
        role="alertdialog"
        onConfirm={() => blocker.proceed?.()}
        onCancel={() => blocker.reset?.()}
      />
    </div>
  );
}
