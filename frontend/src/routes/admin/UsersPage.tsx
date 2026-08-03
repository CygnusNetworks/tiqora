import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toBcp47 } from "@/i18n";
import { useMutation } from "@tanstack/react-query";
import { api, type UserOut, type UserCreate, type UserUpdate } from "@/lib/api";
import { AdminResourcePage } from "@/components/admin/AdminResourcePage";
import type { FieldDef, FieldValues } from "@/components/admin/CrudDrawer";
import type { DataTableColumn } from "@/components/admin/DataTable";
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

  const columns: DataTableColumn<UserOut>[] = [
    { key: "id", header: t("admin.table.id"), mono: true, render: (r) => r.id },
    { key: "login", header: t("admin.users.login"), render: (r) => r.login },
    {
      key: "name",
      header: t("admin.users.name"),
      render: (r) => `${r.first_name} ${r.last_name}`,
    },
    { key: "title", header: t("admin.users.title"), render: (r) => r.title ?? "—" },
    {
      key: "changed",
      header: t("admin.table.changed"),
      render: (r) => formatDateTime(r.change_time, locale),
    },
  ];

  const fields: FieldDef[] = [
    { name: "login", label: t("admin.users.login"), type: "text", required: true },
    {
      name: "password",
      label: t("admin.users.password"),
      type: "password",
      helpText: t("admin.users.passwordHelp"),
    },
    { name: "title", label: t("admin.users.title"), type: "text" },
    { name: "first_name", label: t("admin.users.firstName"), type: "text", required: true },
    { name: "last_name", label: t("admin.users.lastName"), type: "text", required: true },
    {
      name: "valid_id",
      label: t("admin.table.status"),
      type: "select",
      options: [
        { value: 1, label: t("admin.table.valid") },
        { value: 2, label: t("admin.table.invalid") },
      ],
      help: { title: t("admin.table.status"), description: t("admin.help.common.validId") },
    },
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
        <MenuItem
          testId={`admin-row-reset2fa-${row.id}`}
          onSelect={() => setReset2faTarget(row)}
        >
          <span className="inline-flex items-center gap-2">
            <ShieldIcon />
            {t("admin.authConfig.reset2fa")}
          </span>
        </MenuItem>
      )}
      toFormValues={(row) =>
        row
          ? {
              login: row.login,
              password: "",
              title: row.title ?? "",
              first_name: row.first_name,
              last_name: row.last_name,
              valid_id: row.valid_id,
            }
          : { valid_id: 1 }
      }
      toCreateBody={(v: FieldValues): UserCreate => ({
        login: v.login as string,
        password: v.password as string,
        title: (v.title as string) || null,
        first_name: v.first_name as string,
        last_name: v.last_name as string,
        valid_id: Number(v.valid_id) || 1,
      })}
      toUpdateBody={(v: FieldValues): UserUpdate => ({
        login: v.login as string,
        title: (v.title as string) || null,
        first_name: v.first_name as string,
        last_name: v.last_name as string,
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
    </>
  );
}
