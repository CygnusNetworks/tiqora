import { useId, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Dialog } from "@/components/ui/Dialog";
import { Spinner } from "@/components/ui/Spinner";
import { Switch } from "@/components/ui/Switch";
import { api, type RoleOut } from "@/lib/api";
import { apiErrorMessage } from "@/lib/avatarApi";
import {
  dashboardKeys,
  dashboardsApi,
  DashboardConflictError,
  type DashboardTemplate,
  type DashboardTemplateIn,
} from "@/lib/dashboardsApi";

const NAME_MAX = 120;
const INPUT =
  "w-full rounded-md border border-hairline bg-surface-subtle px-3 py-2 text-sm text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";
const LINK_BUTTON =
  "rounded px-1.5 py-1 text-xs text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";

const rolesKey = ["admin", "roles", "dashboard-templates"] as const;

function TemplateDialog({
  template,
  roles,
  rolesFailed,
  onSave,
  onClose,
}: {
  /** The template being edited; `null` creates one. */
  template: DashboardTemplate | null;
  roles: RoleOut[];
  rolesFailed: boolean;
  onSave: (body: DashboardTemplateIn) => Promise<void>;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const ids = useId();
  const [name, setName] = useState(template?.name ?? "");
  const [roleIds, setRoleIds] = useState<number[]>(template?.role_ids ?? []);
  const [locked, setLocked] = useState(template?.locked ?? false);
  const [sort, setSort] = useState(String(template?.sort ?? 0));
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const trimmed = name.trim();
  const nameError = submitted && !trimmed ? t("admin.dashboardTemplates.nameRequired") : null;
  const rolesError = submitted && roleIds.length === 0 ? t("admin.dashboardTemplates.rolesRequired") : null;

  const submit = async (e?: FormEvent) => {
    e?.preventDefault();
    if (pending) return;
    setSubmitted(true);
    if (!trimmed || roleIds.length === 0) return;
    const parsed = Number.parseInt(sort, 10);
    setError(null);
    setPending(true);
    try {
      await onSave({
        name: trimmed,
        icon: template?.icon ?? null,
        role_ids: roleIds,
        locked,
        sort: Number.isFinite(parsed) ? parsed : 0,
      });
    } catch (err) {
      setError(
        err instanceof DashboardConflictError
          ? t("admin.dashboardTemplates.conflict")
          : apiErrorMessage(err, t("admin.dashboardTemplates.saveFailed")),
      );
      setPending(false);
    }
  };

  const toggleRole = (id: number) =>
    setRoleIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));

  return (
    <Dialog
      open
      onClose={onClose}
      title={template ? t("admin.dashboardTemplates.editTitle") : t("admin.dashboardTemplates.newTitle")}
      size="md"
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={pending}>
            {t("admin.dashboardTemplates.cancel")}
          </Button>
          <Button variant="primary" onClick={() => void submit()} disabled={pending}>
            {pending ? <Spinner /> : template ? t("admin.dashboardTemplates.save") : t("admin.dashboardTemplates.create")}
          </Button>
        </>
      }
    >
      <form onSubmit={(e) => void submit(e)} className="space-y-4" noValidate>
        <div className="space-y-1.5">
          <label htmlFor={`${ids}-name`} className="text-[13px] font-medium text-ink">
            {t("admin.dashboardTemplates.name")}
          </label>
          <input
            id={`${ids}-name`}
            type="text"
            value={name}
            maxLength={NAME_MAX}
            onChange={(e) => setName(e.target.value)}
            aria-invalid={nameError ? true : undefined}
            aria-describedby={nameError ? `${ids}-name-err` : undefined}
            className={INPUT}
          />
          {nameError && (
            <p id={`${ids}-name-err`} className="text-xs text-danger">
              {nameError}
            </p>
          )}
        </div>

        <fieldset className="space-y-1.5" aria-describedby={rolesError ? `${ids}-roles-err` : undefined}>
          <legend className="text-[13px] font-medium text-ink">{t("admin.dashboardTemplates.roles")}</legend>
          {rolesFailed ? (
            <p role="alert" className="text-xs text-danger">
              {t("admin.dashboardTemplates.rolesLoadFailed")}
            </p>
          ) : (
            <ul className="max-h-48 space-y-1 overflow-y-auto rounded-md border border-hairline p-2">
              {roles.map((role) => (
                <li key={role.id}>
                  <label className="flex cursor-pointer items-center gap-2 text-sm text-ink">
                    <input type="checkbox" checked={roleIds.includes(role.id)} onChange={() => toggleRole(role.id)} />
                    {role.name}
                  </label>
                </li>
              ))}
            </ul>
          )}
          {rolesError && (
            <p id={`${ids}-roles-err`} className="text-xs text-danger">
              {rolesError}
            </p>
          )}
        </fieldset>

        <div className="flex items-center gap-3">
          <Switch checked={locked} onChange={setLocked} aria-labelledby={`${ids}-locked`} />
          <span id={`${ids}-locked`} className="text-sm text-ink">
            {t("admin.dashboardTemplates.locked")}
          </span>
        </div>

        <div className="space-y-1.5">
          <label htmlFor={`${ids}-sort`} className="text-[13px] font-medium text-ink">
            {t("admin.dashboardTemplates.priority")}
          </label>
          <input
            id={`${ids}-sort`}
            type="number"
            inputMode="numeric"
            value={sort}
            onChange={(e) => setSort(e.target.value)}
            aria-describedby={`${ids}-sort-help`}
            className={`${INPUT} w-28`}
          />
          <p id={`${ids}-sort-help`} className="text-xs text-muted">
            {t("admin.dashboardTemplates.priorityHelp")}
          </p>
        </div>

        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
        <button type="submit" hidden tabIndex={-1} aria-hidden="true" />
      </form>
    </Dialog>
  );
}

/** Admin: the role dashboard templates (which dashboard a role starts with, optionally binding). */
export function DashboardTemplatesPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [editing, setEditing] = useState<DashboardTemplate | "new" | null>(null);
  const [deleting, setDeleting] = useState<DashboardTemplate | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const listQ = useQuery({
    queryKey: dashboardKeys.templates(),
    queryFn: ({ signal }) => dashboardsApi.templates.list(signal),
  });
  const rolesQ = useQuery({
    queryKey: rolesKey,
    queryFn: async ({ signal }) => (await api.adminRoles.list({ valid: "all", pageSize: 500 }, signal)).items,
  });
  const roles = rolesQ.data ?? [];
  const roleName = new Map(roles.map((r) => [r.id, r.name]));

  const refresh = () => qc.invalidateQueries({ queryKey: dashboardKeys.templates() });

  const save = async (body: DashboardTemplateIn) => {
    if (editing && editing !== "new") await dashboardsApi.templates.update(editing.id, body, editing.version);
    else await dashboardsApi.templates.create(body);
    await refresh();
    setEditing(null);
  };

  const remove = useMutation({
    mutationFn: (id: number) => dashboardsApi.templates.remove(id),
    onSuccess: async () => {
      await refresh();
      setDeleting(null);
    },
    onError: (err) => {
      setDeleting(null);
      setDeleteError(apiErrorMessage(err, t("admin.dashboardTemplates.deleteFailed")));
    },
  });

  const templates = listQ.data ?? [];
  const COL = "px-3 py-2 text-left text-[12px] font-semibold text-muted";

  return (
    <div className="mx-auto max-w-5xl space-y-4 p-4" data-testid="admin-dashboard-templates-page">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="font-display text-xl font-semibold text-ink">{t("admin.dashboardTemplates.title")}</h1>
          <p className="mt-1 text-sm text-muted">{t("admin.dashboardTemplates.subtitle")}</p>
        </div>
        <Button variant="primary" onClick={() => setEditing("new")}>
          {t("admin.dashboardTemplates.new")}
        </Button>
      </div>

      {deleteError && (
        <p role="alert" className="text-sm text-danger">
          {deleteError}
        </p>
      )}
      {listQ.isLoading && <Spinner />}
      {listQ.isError && (
        <p role="alert" className="text-sm text-danger">
          {t("admin.dashboardTemplates.loadFailed")}
        </p>
      )}
      {listQ.isSuccess && templates.length === 0 && (
        <p className="rounded-[14px] border border-dashed border-hairline bg-surface px-4 py-8 text-center text-sm text-muted">
          {t("admin.dashboardTemplates.empty")}
        </p>
      )}
      {templates.length > 0 && (
        <div className="overflow-x-auto rounded-[14px] border border-hairline bg-surface">
          <table className="w-full text-sm">
            <thead className="border-b border-hairline">
              <tr>
                <th scope="col" className={COL}>{t("admin.dashboardTemplates.columns.name")}</th>
                <th scope="col" className={COL}>{t("admin.dashboardTemplates.columns.roles")}</th>
                <th scope="col" className={COL}>{t("admin.dashboardTemplates.columns.locked")}</th>
                <th scope="col" className={COL}>{t("admin.dashboardTemplates.columns.widgets")}</th>
                <th scope="col" className={COL}>{t("admin.dashboardTemplates.columns.priority")}</th>
                <th scope="col" className={COL}>
                  <span className="sr-only">{t("admin.dashboardTemplates.columns.actions")}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {templates.map((tpl) => {
                const nameId = `dashboard-template-${tpl.id}-name`;
                const names = tpl.role_ids.map((id) => roleName.get(id) ?? `#${id}`);
                return (
                  <tr key={tpl.id} className="border-b border-hairline last:border-0">
                    <th scope="row" id={nameId} className="px-3 py-2 text-left font-medium text-ink">
                      {tpl.name}
                    </th>
                    <td className="px-3 py-2 text-ink">
                      {names.length > 0 ? names.join(", ") : <span className="text-muted">{t("admin.dashboardTemplates.noRoles")}</span>}
                    </td>
                    <td className="px-3 py-2 text-ink">{tpl.locked ? t("admin.dashboardTemplates.yes") : t("admin.dashboardTemplates.no")}</td>
                    <td className="px-3 py-2 tabular-nums text-ink">{tpl.widget_count}</td>
                    <td className="px-3 py-2 tabular-nums text-ink">{tpl.sort}</td>
                    <td className="px-3 py-2">
                      <div className="flex flex-wrap justify-end gap-1">
                        <Link to="/agent" search={{ d: tpl.id }} aria-describedby={nameId} className={LINK_BUTTON}>
                          {t("admin.dashboardTemplates.editLayout")}
                        </Link>
                        <button type="button" aria-describedby={nameId} className={LINK_BUTTON} onClick={() => setEditing(tpl)}>
                          {t("admin.dashboardTemplates.edit")}
                        </button>
                        <button
                          type="button"
                          aria-describedby={nameId}
                          className={`${LINK_BUTTON} !text-danger`}
                          onClick={() => {
                            setDeleteError(null);
                            setDeleting(tpl);
                          }}
                        >
                          {t("admin.dashboardTemplates.delete")}
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {editing && (
        <TemplateDialog
          template={editing === "new" ? null : editing}
          roles={roles.filter((r) => r.valid_id === 1 || (editing !== "new" && editing.role_ids.includes(r.id)))}
          rolesFailed={rolesQ.isError}
          onSave={save}
          onClose={() => setEditing(null)}
        />
      )}
      <ConfirmDialog
        open={deleting !== null}
        role="alertdialog"
        variant="danger"
        title={t("admin.dashboardTemplates.deleteTitle", { name: deleting?.name ?? "" })}
        message={t("admin.dashboardTemplates.deleteMessage")}
        confirmLabel={t("admin.dashboardTemplates.delete")}
        pending={remove.isPending}
        onConfirm={() => deleting && remove.mutate(deleting.id)}
        onCancel={() => setDeleting(null)}
      />
    </div>
  );
}
