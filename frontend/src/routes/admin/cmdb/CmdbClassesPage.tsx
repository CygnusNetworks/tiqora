import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { KEY_PATTERN, exampleNumber, suggestClassKey } from "@/components/cmdb/adminModel";
import { CmdbModeNotice } from "@/components/cmdb/CmdbModeNotice";
import { JobDialog } from "@/components/jobs/JobDialog";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { SelectField } from "@/components/ui/SelectField";
import { Spinner } from "@/components/ui/Spinner";
import { Switch } from "@/components/ui/Switch";
import { api } from "@/lib/api";
import { apiErrorMessage } from "@/lib/avatarApi";
import type { CmdbClass } from "@/lib/cmdbApi";
import { cmdbAdminApi } from "@/lib/cmdbAdminApi";

const INPUT = "w-full rounded border border-hairline bg-surface px-2 py-1.5 text-sm text-ink focus:outline-none focus:ring-1 focus:ring-accent";
const LABEL = "mb-1 block text-xs font-medium uppercase tracking-wide text-muted";

function useGroupItems() {
  const groupsQ = useQuery({
    queryKey: ["admin", "groups", "ref"],
    queryFn: ({ signal }) => api.adminGroups.list({ valid: "valid", pageSize: 500 }, signal),
  });
  const items = (groupsQ.data?.items ?? []).map((g) => ({ value: g.id, label: g.name }));
  return items;
}

function NewClassDialog({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (c: CmdbClass) => void }) {
  const { t } = useTranslation();
  const groups = useGroupItems();
  const [name, setName] = useState("");
  const [key, setKey] = useState("");
  const [keyTouched, setKeyTouched] = useState(false);
  const [groupId, setGroupId] = useState<number | null>(null);
  const [prefix, setPrefix] = useState("");
  const [portal, setPortal] = useState(false);

  const createM = useMutation({
    mutationFn: () =>
      cmdbAdminApi.createClass({
        class_key: key,
        name: name.trim(),
        group_id: groupId as number,
        number_prefix: prefix.trim() || null,
        portal_visible: portal,
        position: 0,
        valid: true,
      }),
    onSuccess: onCreated,
  });
  const keyOk = KEY_PATTERN.test(key);
  const canSave = name.trim() !== "" && keyOk && groupId !== null && !createM.isPending;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t("admin.cmdb.classes.new")}
      size="md"
      footer={
        <>
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button variant="primary" disabled={!canSave} onClick={() => createM.mutate()} data-testid="cmdb-class-save">
            {t("admin.cmdb.classes.create")}
          </Button>
        </>
      }
    >
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (canSave) createM.mutate();
        }}
      >
        <div>
          <label className={LABEL} htmlFor="cmdb-class-name">{t("admin.cmdb.classes.name")}</label>
          <input
            id="cmdb-class-name"
            data-testid="cmdb-class-name"
            className={INPUT}
            value={name}
            maxLength={200}
            onChange={(e) => {
              setName(e.target.value);
              if (!keyTouched) setKey(suggestClassKey(e.target.value));
            }}
          />
        </div>
        <div>
          <label className={LABEL} htmlFor="cmdb-class-key">{t("admin.cmdb.classes.key")}</label>
          <input
            id="cmdb-class-key"
            data-testid="cmdb-class-key"
            className={`${INPUT} font-mono`}
            value={key}
            maxLength={50}
            aria-invalid={key !== "" && !keyOk}
            onChange={(e) => {
              setKeyTouched(true);
              setKey(e.target.value);
            }}
          />
          <p className="mt-1 text-xs text-muted">{t("admin.cmdb.classes.keyHelp")}</p>
        </div>
        <div>
          <label className={LABEL} htmlFor="cmdb-class-group">{t("admin.cmdb.classes.group")}</label>
          <SelectField<number> id="cmdb-class-group" testId="cmdb-class-group" items={groups} value={groupId} onChange={setGroupId} placeholder={t("admin.cmdb.classes.groupPlaceholder")} />
          <p className="mt-1 text-xs text-muted">{t("admin.cmdb.classes.groupHelp")}</p>
        </div>
        <div>
          <label className={LABEL} htmlFor="cmdb-class-prefix">{t("admin.cmdb.classes.prefix")}</label>
          <input id="cmdb-class-prefix" data-testid="cmdb-class-prefix" className={`${INPUT} font-mono`} value={prefix} maxLength={20} onChange={(e) => setPrefix(e.target.value)} />
          <p className="mt-1 text-xs text-muted" data-testid="cmdb-class-prefix-example">
            {t("admin.cmdb.classes.prefixExample", { example: exampleNumber(prefix, key) })}
          </p>
        </div>
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-sm text-ink">{t("admin.cmdb.classes.portal")}</div>
            <p className="text-xs text-muted">{t("admin.cmdb.classes.portalHelp")}</p>
          </div>
          <Switch checked={portal} onChange={setPortal} aria-label={t("admin.cmdb.classes.portal")} testId="cmdb-class-portal" />
        </div>
        {createM.isError && (
          <p role="alert" className="text-sm text-danger" data-testid="cmdb-class-error">
            {apiErrorMessage(createM.error, t("admin.cmdb.classes.saveError"))}
          </p>
        )}
      </form>
    </Dialog>
  );
}

function TemplateDialog({ open, onClose, onApplied }: { open: boolean; onClose: () => void; onApplied: (c: CmdbClass) => void }) {
  const { t } = useTranslation();
  const groups = useGroupItems();
  const templatesQ = useQuery({ queryKey: ["admin", "cmdb", "class-templates"], queryFn: () => cmdbAdminApi.classTemplates(), enabled: open });
  const [templateKey, setTemplateKey] = useState<string | null>(null);
  const [groupId, setGroupId] = useState<number | null>(null);
  const applyM = useMutation({
    mutationFn: () => cmdbAdminApi.applyClassTemplate(templateKey as string, groupId as number),
    onSuccess: onApplied,
  });
  const canApply = templateKey !== null && groupId !== null && !applyM.isPending;
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t("admin.cmdb.classes.fromTemplate")}
      description={t("admin.cmdb.classes.templateHelp")}
      size="md"
      footer={
        <>
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button variant="primary" disabled={!canApply} onClick={() => applyM.mutate()} data-testid="cmdb-template-apply">
            {t("admin.cmdb.classes.applyTemplate")}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <div>
          <span className={LABEL}>{t("admin.cmdb.classes.template")}</span>
          {templatesQ.isLoading ? (
            <Spinner />
          ) : (
            <ul className="space-y-1" role="radiogroup" aria-label={t("admin.cmdb.classes.template")} data-testid="cmdb-template-list">
              {(templatesQ.data ?? []).map((tpl) => (
                <li key={tpl.key}>
                  <button
                    type="button"
                    role="radio"
                    aria-checked={templateKey === tpl.key}
                    data-testid={`cmdb-template-${tpl.key}`}
                    onClick={() => setTemplateKey(tpl.key)}
                    className={`flex w-full items-center justify-between rounded border px-3 py-2 text-left text-sm ${templateKey === tpl.key ? "border-accent bg-accent-dim text-ink" : "border-hairline bg-surface text-ink hover:bg-surface-subtle"}`}
                  >
                    <span className="font-medium">{tpl.name}</span>
                    <span className="text-xs text-muted">{t("admin.cmdb.classes.attributeCount", { count: tpl.attribute_count })}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div>
          <label className={LABEL} htmlFor="cmdb-template-group">{t("admin.cmdb.classes.group")}</label>
          <SelectField<number> id="cmdb-template-group" testId="cmdb-template-group" items={groups} value={groupId} onChange={setGroupId} placeholder={t("admin.cmdb.classes.groupPlaceholder")} />
        </div>
        {applyM.isError && (
          <p role="alert" className="text-sm text-danger" data-testid="cmdb-template-error">
            {apiErrorMessage(applyM.error, t("admin.cmdb.classes.saveError"))}
          </p>
        )}
      </div>
    </Dialog>
  );
}

/** Admin list of the CMDB classes (D10): create, create from template, seed defaults, settings. */
export function CmdbClassesPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [newOpen, setNewOpen] = useState(false);
  const [templateOpen, setTemplateOpen] = useState(false);

  const statusQ = useQuery({ queryKey: ["admin", "cmdb", "status"], queryFn: () => cmdbAdminApi.status() });
  const active = statusQ.data?.mode === "tiqora";
  const classesQ = useQuery({ queryKey: ["admin", "cmdb", "classes"], queryFn: () => cmdbAdminApi.classes(), enabled: active });
  const seedM = useMutation({
    mutationFn: () => cmdbAdminApi.seedDefaults(),
    onSuccess: async (status) => {
      qc.setQueryData(["admin", "cmdb", "status"], status);
      await qc.invalidateQueries({ queryKey: ["admin", "cmdb"] });
      void qc.invalidateQueries({ queryKey: ["cmdb"] });
    },
  });

  const [recomputeJob, setRecomputeJob] = useState<number | null>(null);
  const recomputeM = useMutation({
    mutationFn: () => cmdbAdminApi.recomputePropagation(),
    onSuccess: (accepted) => setRecomputeJob(accepted.job_id),
  });

  const openEditor = (c: CmdbClass) => {
    void qc.invalidateQueries({ queryKey: ["admin", "cmdb", "classes"] });
    void qc.invalidateQueries({ queryKey: ["cmdb"] });
    void navigate({ to: "/admin/cmdb/classes/$classId", params: { classId: String(c.id) } });
  };

  const status = statusQ.data;
  const needsSeed = active && status && (!status.catalog_seeded || !status.views_present);
  const rows = classesQ.data ?? [];

  return (
    <div className="mx-auto max-w-5xl space-y-4 p-4 sm:p-6" data-testid="admin-cmdb-classes-page">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h1 className="font-display text-xl font-semibold text-ink">{t("admin.cmdb.classes.title")}</h1>
          <p className="mt-1 text-xs text-muted">{t("admin.cmdb.classes.subtitle")}</p>
        </div>
        {active && (
          <div className="flex flex-wrap items-center gap-2">
            <Link to="/admin/settings/$section" params={{ section: "cmdb" }} className="rounded border border-hairline bg-surface px-3 py-1.5 text-sm text-ink hover:bg-surface-subtle" data-testid="cmdb-settings-link">
              {t("admin.cmdb.classes.settings")}
            </Link>
            <Button onClick={() => setTemplateOpen(true)} data-testid="cmdb-from-template">{t("admin.cmdb.classes.fromTemplate")}</Button>
            <Button variant="primary" onClick={() => setNewOpen(true)} data-testid="cmdb-new-class">{t("admin.cmdb.classes.new")}</Button>
          </div>
        )}
      </div>

      {status && <CmdbModeNotice status={status} />}

      {needsSeed && (
        <div role="status" className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-hairline bg-surface-subtle px-4 py-3 text-sm text-ink" data-testid="cmdb-seed-banner">
          <div className="space-y-0.5">
            {!status.catalog_seeded && <p>{t("admin.cmdb.seed.catalogMissing")}</p>}
            {!status.views_present && <p>{t("admin.cmdb.seed.viewsMissing")}</p>}
          </div>
          <Button variant="primary" disabled={seedM.isPending} onClick={() => seedM.mutate()} data-testid="cmdb-seed-defaults">
            {t("admin.cmdb.seed.button")}
          </Button>
        </div>
      )}
      {seedM.isError && (
        <p role="alert" className="text-sm text-danger">{apiErrorMessage(seedM.error, t("admin.cmdb.seed.error"))}</p>
      )}

      {active && (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs text-muted" data-testid="cmdb-linktypes-hint">
            {t("admin.cmdb.classes.linkTypesHint")}{" "}
            <a href="/admin/itsm/link-types" className="text-accent hover:underline" data-testid="cmdb-linktypes-link">
              {t("admin.cmdb.classes.linkTypesLink")}
            </a>
          </p>
          <Button
            disabled={recomputeM.isPending}
            onClick={() => recomputeM.mutate()}
            title={t("admin.cmdb.classes.recomputeHint")}
            data-testid="cmdb-recompute"
          >
            {t("admin.cmdb.classes.recompute")}
          </Button>
        </div>
      )}
      {recomputeM.isError && (
        <p role="alert" className="text-sm text-danger">{apiErrorMessage(recomputeM.error, t("admin.cmdb.classes.recomputeError"))}</p>
      )}
      <JobDialog
        jobId={recomputeJob}
        title={t("jobs.kinds.core.cmdb_propagation")}
        testId="cmdb-recompute-job"
        onClose={() => setRecomputeJob(null)}
      />

      {active && (
        <div className="overflow-x-auto rounded-xl border border-hairline bg-surface" data-testid="cmdb-classes-table">
          {classesQ.isLoading ? (
            <div className="flex justify-center p-6"><Spinner /></div>
          ) : rows.length === 0 ? (
            <p className="p-4 text-sm text-muted">{t("cmdb.noClasses")}</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-hairline text-left text-xs uppercase tracking-wide text-muted">
                  <th className="px-3 py-2 font-medium">{t("admin.cmdb.classes.key")}</th>
                  <th className="px-3 py-2 font-medium">{t("admin.cmdb.classes.name")}</th>
                  <th className="hidden px-3 py-2 font-medium sm:table-cell">{t("admin.cmdb.classes.group")}</th>
                  <th className="px-3 py-2 font-medium">{t("admin.table.status")}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((c) => (
                  <tr key={c.id} className="border-b border-hairline last:border-0" data-testid={`cmdb-class-row-${c.class_key}`}>
                    <td className="px-3 py-2 font-mono text-xs text-muted">{c.class_key}</td>
                    <td className="px-3 py-2">
                      <Link to="/admin/cmdb/classes/$classId" params={{ classId: String(c.id) }} className="font-medium text-accent hover:underline">
                        {c.name}
                      </Link>
                    </td>
                    <td className="hidden px-3 py-2 text-ink sm:table-cell">{c.group_name ?? c.group_id}</td>
                    <td className="px-3 py-2">
                      <Badge tone={c.valid ? "success" : "muted"}>{c.valid ? t("admin.table.valid") : t("admin.table.invalid")}</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {newOpen && <NewClassDialog open onClose={() => setNewOpen(false)} onCreated={openEditor} />}
      {templateOpen && <TemplateDialog open onClose={() => setTemplateOpen(false)} onApplied={openEditor} />}
    </div>
  );
}
