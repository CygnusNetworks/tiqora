import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  api,
  type OAuth2TokenConfigCreate,
  type OAuth2TokenConfigOut,
  type OAuth2TokenConfigUpdate,
} from "@/lib/api";
import { AdminResourcePage } from "@/components/admin/AdminResourcePage";
import type { FieldDef, FieldValues } from "@/components/admin/CrudDrawer";
import type { DataTableColumn } from "@/components/admin/DataTable";
import { MenuItem } from "@/components/ui/Menu";
import { formatDateTime } from "@/lib/format";
import { toBcp47 } from "@/i18n";

function statusLabel(
  status: string,
  t: (k: string) => string,
): string {
  switch (status) {
    case "valid":
      return t("admin.oauth2.tokenStatusValid");
    case "expired":
      return t("admin.oauth2.tokenStatusExpired");
    case "needs_reauth":
      return t("admin.oauth2.tokenStatusNeedsReauth");
    case "error":
      return t("admin.oauth2.tokenStatusError");
    default:
      return t("admin.oauth2.tokenStatusNone");
  }
}

export function OAuth2TokensPage() {
  const { t, i18n } = useTranslation();
  const locale = toBcp47(i18n.language);
  const qc = useQueryClient();
  const [busyId, setBusyId] = useState<number | null>(null);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);

  const templatesQ = useQuery({
    queryKey: ["admin", "oauth2-templates"],
    queryFn: ({ signal }) => api.adminOAuth2TokenConfigs.templates(signal),
    staleTime: 60_000,
  });

  const authorizeMut = useMutation({
    mutationFn: (id: number) => api.adminOAuth2TokenConfigs.authorizeUrl(id),
    onSuccess: (res) => {
      setStatusMsg(t("admin.oauth2.authorizeOpened"));
      window.open(res.url, "_blank", "noopener,noreferrer");
    },
    onError: (err) => setStatusMsg(String(err)),
    onSettled: () => setBusyId(null),
  });

  const refreshMut = useMutation({
    mutationFn: (id: number) => api.adminOAuth2TokenConfigs.refresh(id),
    onSuccess: () => {
      setStatusMsg(t("admin.oauth2.refreshSuccess"));
      void qc.invalidateQueries({ queryKey: ["admin", "oauth2-token-configs"] });
    },
    onError: (err) => setStatusMsg(String(err)),
    onSettled: () => setBusyId(null),
  });

  const columns: DataTableColumn<OAuth2TokenConfigOut>[] = [
    { key: "id", header: t("admin.table.id"), mono: true, render: (r) => r.id },
    { key: "name", header: t("admin.oauth2.name"), render: (r) => r.name },
    {
      key: "client",
      header: t("admin.oauth2.clientId"),
      mono: true,
      render: (r) => r.client_id || "—",
    },
    {
      key: "token",
      header: t("admin.oauth2.tokenStatus"),
      render: (r) => statusLabel(r.token_status, t),
    },
    {
      key: "changed",
      header: t("admin.table.changed"),
      render: (r) => formatDateTime(r.change_time, locale),
    },
  ];

  const templateOptions = (templatesQ.data ?? []).map((tpl) => ({
    value: tpl.id,
    label: tpl.name,
  }));

  const fields: FieldDef[] = [
    { name: "name", label: t("admin.oauth2.name"), type: "text", required: true },
    {
      name: "template_id",
      label: t("admin.oauth2.template"),
      type: "select",
      options: [{ value: "", label: t("admin.oauth2.templateNone") }, ...templateOptions],
      helpText: t("admin.oauth2.templateHelp"),
    },
    { name: "client_id", label: t("admin.oauth2.clientId"), type: "text", required: true },
    {
      name: "client_secret",
      label: t("admin.oauth2.clientSecret"),
      type: "password",
      helpText: t("admin.oauth2.clientSecretHelp"),
    },
    {
      name: "scope",
      label: t("admin.oauth2.scope"),
      type: "text",
      helpText: t("admin.oauth2.scopeHelp"),
    },
    {
      name: "redirect_uri",
      label: t("admin.oauth2.redirectUri"),
      type: "text",
      helpText: t("admin.oauth2.redirectUriHelp"),
      hideOnCreate: false,
    },
    { name: "valid", label: t("admin.table.valid"), type: "checkbox" },
  ];

  return (
    <div className="space-y-3">
      {statusMsg && (
        <p className="rounded-md border border-hairline bg-surface-subtle px-3 py-2 text-sm text-ink">
          {statusMsg}
        </p>
      )}
      <AdminResourcePage
        resourceKey="oauth2-token-configs"
        title={t("admin.oauth2.title_plural")}
        newLabel={t("admin.oauth2.new")}
        api={api.adminOAuth2TokenConfigs}
        idOf={(r) => r.id}
        columns={columns}
        fields={fields}
        isRowValid={(r) => r.valid}
        toFormValues={(row) =>
          row
            ? {
                name: row.name,
                template_id: "",
                client_id: row.client_id,
                client_secret: "",
                scope: row.scope,
                redirect_uri: row.redirect_uri,
                valid: row.valid,
              }
            : {
                valid: true,
                template_id: "microsoft-exchange-online",
                redirect_uri: templatesQ.data?.[0]
                  ? ""
                  : "",
              }
        }
        toCreateBody={(v: FieldValues): OAuth2TokenConfigCreate => ({
          name: v.name as string,
          client_id: (v.client_id as string) || undefined,
          client_secret: (v.client_secret as string) || undefined,
          scope: (v.scope as string) || undefined,
          template_id: (v.template_id as string) || undefined,
          valid: Boolean(v.valid ?? true),
        })}
        toUpdateBody={(v: FieldValues): OAuth2TokenConfigUpdate => ({
          name: v.name as string,
          client_id: (v.client_id as string) || undefined,
          ...(v.client_secret ? { client_secret: v.client_secret as string } : {}),
          scope: (v.scope as string) || undefined,
          valid: Boolean(v.valid ?? true),
        })}
        rowActions={(row) => (
          <>
            <MenuItem
              testId={`admin-oauth2-authorize-${row.id}`}
              onSelect={() => {
                setBusyId(row.id);
                authorizeMut.mutate(row.id);
              }}
            >
              {busyId === row.id ? t("admin.oauth2.working") : t("admin.oauth2.authorize")}
            </MenuItem>
            <MenuItem
              testId={`admin-oauth2-refresh-${row.id}`}
              onSelect={() => {
                if (!row.has_refresh_token) return;
                setBusyId(row.id);
                refreshMut.mutate(row.id);
              }}
            >
              {t("admin.oauth2.refresh")}
            </MenuItem>
          </>
        )}
      />
    </div>
  );
}
