import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toBcp47 } from "@/i18n";
import { useMutation } from "@tanstack/react-query";
import { api, type UserOut, type UserCreate, type UserUpdate } from "@/lib/api";
import { AdminResourcePage } from "@/components/admin/AdminResourcePage";
import type { FieldDef, FieldValues } from "@/components/admin/CrudDrawer";
import type { DataTableColumn } from "@/components/admin/DataTable";
import { EffectivePermissionsDialog } from "@/components/admin/EffectivePermissionsDialog";
import { AgentSettingsDialog } from "@/components/admin/AgentSettingsDialog";
import { UserDeleteDialog } from "@/components/admin/UserDeleteDialog";
import { MenuItem } from "@/components/ui/Menu";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { formatDateTime } from "@/lib/format";

function ShieldIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" aria-hidden="true">
      <path
        d="M12 3 5 6v5c0 4.2 2.9 7.6 7 9 4.1-1.4 7-4.8 7-9V6l-7-3Z"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function KeyIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" aria-hidden="true">
      <path
        d="M14.5 9.5a4 4 0 1 0-4 4h.5L14 16v2h2v-2h2v-2h1l1-1-1.5-1.5"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" aria-hidden="true">
      <path
        d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13M10 11v6M14 11v6"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function SlidersIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" aria-hidden="true">
      <path
        d="M4 6h9m4 0h3M4 12h3m4 0h9M4 18h13m4 0h-1"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
      />
      <circle cx="15" cy="6" r="2" stroke="currentColor" strokeWidth="1.7" />
      <circle cx="9" cy="12" r="2" stroke="currentColor" strokeWidth="1.7" />
      <circle cx="19" cy="18" r="2" stroke="currentColor" strokeWidth="1.7" />
    </svg>
  );
}

export function UsersPage() {
  const { t, i18n } = useTranslation();
  const locale = toBcp47(i18n.language);

  // Per-user 2FA reset — same endpoint as the dedicated auth-config page, but
  // reachable straight from the user list. A confirmation guards the reset.
  const [reset2faTarget, setReset2faTarget] = useState<UserOut | null>(null);
  const resetM = useMutation({
    mutationFn: (userId: number) => api.adminAuthConfig.reset2fa(userId),
    onSuccess: () => setReset2faTarget(null),
  });

  const [permissionsTarget, setPermissionsTarget] = useState<UserOut | null>(null);
  const [settingsTarget, setSettingsTarget] = useState<UserOut | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<UserOut | null>(null);

  const columns: DataTableColumn<UserOut>[] = [
    { key: "id", header: t("admin.table.id"), mono: true, render: (r) => r.id },
    { key: "login", header: t("admin.users.login"), render: (r) => r.login },
    {
      key: "name",
      header: t("admin.users.name"),
      render: (r) => `${r.first_name} ${r.last_name}`,
    },
    { key: "email", header: t("admin.users.email"), render: (r) => r.email ?? "—" },
    { key: "title", header: t("admin.users.title"), render: (r) => r.title ?? "—" },
    {
      key: "changed",
      header: t("admin.table.changed"),
      render: (r) => formatDateTime(r.change_time, locale),
    },
  ];

  // Two tabs instead of three stacked sections: the form was tall enough to
  // scroll, and "Status" appeared twice (once as a section heading, once as
  // the field label). Account-shaped fields go left, person-shaped right.
  const tabAccount = t("admin.users.tabAccount");
  const tabPerson = t("admin.users.tabPerson");

  const fields: FieldDef[] = [
    {
      name: "login",
      label: t("admin.users.login"),
      type: "text",
      required: true,
      tab: tabAccount,
    },
    {
      name: "password_mode",
      label: t("admin.users.passwordMode"),
      type: "select",
      hideOnEdit: true,
      tab: tabAccount,
      helpText: (v) =>
        v.password_mode === "auto" ? t("admin.users.passwordModeAutoHelp") : undefined,
      options: [
        { value: "auto", label: t("admin.users.passwordModeAuto") },
        { value: "manual", label: t("admin.users.passwordModeManual") },
      ],
    },
    {
      name: "password",
      label: t("admin.users.password"),
      type: "password",
      helpText: t("admin.users.passwordHelp"),
      showIf: (v) => v.password_mode !== "auto",
      required: (v) => v.password_mode === "manual",
      tab: tabAccount,
    },
    {
      name: "valid_id",
      label: t("admin.table.status"),
      type: "select",
      tab: tabAccount,
      options: [
        { value: 1, label: t("admin.table.valid") },
        { value: 2, label: t("admin.table.invalid") },
      ],
      help: { title: t("admin.table.status"), description: t("admin.help.common.validId") },
    },

    {
      name: "first_name",
      label: t("admin.users.firstName"),
      type: "text",
      required: true,
      width: "half",
      tab: tabPerson,
    },
    {
      name: "last_name",
      label: t("admin.users.lastName"),
      type: "text",
      required: true,
      width: "half",
      tab: tabPerson,
    },
    {
      name: "email",
      label: t("admin.users.email"),
      type: "text",
      required: (v) => v.password_mode === "auto",
      tab: tabPerson,
    },
    { name: "mobile", label: t("admin.users.mobile"), type: "text", width: "half", tab: tabPerson },
    { name: "title", label: t("admin.users.title"), type: "text", width: "half", tab: tabPerson },
  ];

  return (
    <>
    <AdminResourcePage
      resourceKey="users"
      title={t("admin.users.title_plural")}
      newLabel={t("admin.users.new")}
      api={api.adminUsers}
      idOf={(r) => r.id}
      columns={columns}
      fields={fields}
      rowActions={(row) => (
        <>
          <MenuItem
            testId={`admin-row-effective-permissions-${row.id}`}
            onSelect={() => setPermissionsTarget(row)}
          >
            <span className="inline-flex items-center gap-2">
              <SlidersIcon />
              {t("admin.users.effectivePermissions")}
            </span>
          </MenuItem>
          <MenuItem
            testId={`admin-row-edit-settings-${row.id}`}
            onSelect={() => setSettingsTarget(row)}
          >
            <span className="inline-flex items-center gap-2">
              <KeyIcon />
              {t("admin.users.editSettings")}
            </span>
          </MenuItem>
          <MenuItem
            testId={`admin-row-reset2fa-${row.id}`}
            onSelect={() => setReset2faTarget(row)}
          >
            <span className="inline-flex items-center gap-2">
              <ShieldIcon />
              {t("admin.authConfig.reset2fa")}
            </span>
          </MenuItem>
          <MenuItem
            testId={`admin-row-delete-permanent-${row.id}`}
            onSelect={() => setDeleteTarget(row)}
          >
            <span className="inline-flex items-center gap-2 text-danger">
              <TrashIcon />
              {t("admin.users.deletePermanent")}
            </span>
          </MenuItem>
        </>
      )}
      toFormValues={(row) =>
        row
          ? {
              login: row.login,
              title: row.title ?? "",
              first_name: row.first_name,
              last_name: row.last_name,
              email: row.email ?? "",
              mobile: row.mobile ?? "",
              valid_id: row.valid_id,
            }
          : { valid_id: 1, password_mode: "auto" }
      }
      toCreateBody={(v: FieldValues): UserCreate => ({
        login: v.login as string,
        password: v.password_mode === "manual" ? (v.password as string) : null,
        title: (v.title as string) || null,
        first_name: v.first_name as string,
        last_name: v.last_name as string,
        email: (v.email as string) || null,
        mobile: (v.mobile as string) || null,
        valid_id: Number(v.valid_id) || 1,
      })}
      toUpdateBody={(v: FieldValues): UserUpdate => ({
        login: v.login as string,
        title: (v.title as string) || null,
        first_name: v.first_name as string,
        last_name: v.last_name as string,
        email: (v.email as string) || null,
        mobile: (v.mobile as string) || null,
        valid_id: Number(v.valid_id) || 1,
        ...(v.password ? { password: v.password as string } : {}),
      })}
    />
    <ConfirmDialog
      open={reset2faTarget !== null}
      variant="danger"
      title={t("admin.authConfig.resetConfirmTitle")}
      message={t("admin.authConfig.resetConfirmBody", {
        login: reset2faTarget?.login ?? "",
      })}
      confirmLabel={t("admin.authConfig.reset2fa")}
      pending={resetM.isPending}
      onConfirm={() => {
        if (reset2faTarget) resetM.mutate(reset2faTarget.id);
      }}
      onCancel={() => setReset2faTarget(null)}
    />
    <EffectivePermissionsDialog
      user={permissionsTarget}
      onClose={() => setPermissionsTarget(null)}
    />
    <AgentSettingsDialog user={settingsTarget} onClose={() => setSettingsTarget(null)} />
    <UserDeleteDialog
      user={deleteTarget}
      onClose={() => setDeleteTarget(null)}
      onDeleted={() => setDeleteTarget(null)}
    />
    </>
  );
}
