import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  api,
  type MailAccountCreate,
  type MailAccountOut,
  type MailAccountUpdate,
} from "@/lib/api";
import { AdminResourcePage } from "@/components/admin/AdminResourcePage";
import type { FieldDef, FieldValues } from "@/components/admin/CrudDrawer";
import type { DataTableColumn } from "@/components/admin/DataTable";
import { formatDateTime } from "@/lib/format";
import { toBcp47 } from "@/i18n";

export function MailAccountsPage() {
  const { t, i18n } = useTranslation();
  const locale = toBcp47(i18n.language);

  const oauthConfigsQ = useQuery({
    queryKey: ["admin", "oauth2-token-configs", "picker"],
    queryFn: ({ signal }) =>
      api.adminOAuth2TokenConfigs.list({ page: 1, pageSize: 200, valid: "valid" }, signal),
    staleTime: 30_000,
  });

  const queuesQ = useQuery({
    queryKey: ["admin", "queues", "picker"],
    queryFn: ({ signal }) => api.adminQueues.list({ page: 1, pageSize: 200, valid: "valid" }, signal),
    staleTime: 60_000,
  });

  const oauthOptions = (oauthConfigsQ.data?.items ?? []).map((c) => ({
    value: String(c.id),
    label: c.name,
  }));
  const queueOptions = (queuesQ.data?.items ?? []).map((q) => ({
    value: String(q.id),
    label: q.name,
  }));

  const columns: DataTableColumn<MailAccountOut>[] = [
    { key: "id", header: t("admin.table.id"), mono: true, render: (r) => r.id },
    { key: "login", header: t("admin.mailAccounts.login"), render: (r) => r.login },
    { key: "host", header: t("admin.mailAccounts.host"), mono: true, render: (r) => r.host },
    {
      key: "type",
      header: t("admin.mailAccounts.accountType"),
      render: (r) => r.account_type,
    },
    {
      key: "auth",
      header: t("admin.mailAccounts.authType"),
      render: (r) =>
        r.authentication_type === "oauth2_token"
          ? t("admin.mailAccounts.authOAuth2")
          : t("admin.mailAccounts.authPassword"),
    },
    {
      key: "changed",
      header: t("admin.table.changed"),
      render: (r) => formatDateTime(r.change_time, locale),
    },
  ];

  const fields: FieldDef[] = [
    { name: "login", label: t("admin.mailAccounts.login"), type: "text", required: true },
    {
      name: "pw",
      label: t("admin.mailAccounts.password"),
      type: "password",
      helpText: t("admin.mailAccounts.passwordHelp"),
    },
    { name: "host", label: t("admin.mailAccounts.host"), type: "text", required: true },
    {
      name: "account_type",
      label: t("admin.mailAccounts.accountType"),
      type: "select",
      options: [
        { value: "IMAPS", label: "IMAPS" },
        { value: "IMAP", label: "IMAP" },
        { value: "POP3S", label: "POP3S" },
        { value: "POP3", label: "POP3" },
      ],
      required: true,
    },
    {
      name: "queue_id",
      label: t("admin.mailAccounts.queue"),
      type: "select",
      options: queueOptions,
      required: true,
    },
    {
      name: "imap_folder",
      label: t("admin.mailAccounts.imapFolder"),
      type: "text",
    },
    {
      name: "authentication_type",
      label: t("admin.mailAccounts.authType"),
      type: "select",
      options: [
        { value: "password", label: t("admin.mailAccounts.authPassword") },
        { value: "oauth2_token", label: t("admin.mailAccounts.authOAuth2") },
      ],
      required: true,
    },
    {
      name: "oauth2_token_config_id",
      label: t("admin.mailAccounts.oauthConfig"),
      type: "select",
      options: [{ value: "", label: "—" }, ...oauthOptions],
      helpText: t("admin.mailAccounts.oauthConfigHelp"),
    },
    {
      name: "trusted",
      label: t("admin.mailAccounts.trusted"),
      type: "checkbox",
      helpText: t("admin.mailAccounts.trustedHelp"),
    },
    { name: "comments", label: t("admin.mailAccounts.comments"), type: "text" },
    { name: "valid", label: t("admin.table.valid"), type: "checkbox" },
  ];

  return (
    <AdminResourcePage
      resourceKey="mail-accounts"
      title={t("admin.mailAccounts.title_plural")}
      newLabel={t("admin.mailAccounts.new")}
      api={api.adminMailAccounts}
      idOf={(r) => r.id}
      columns={columns}
      fields={fields}
      isRowValid={(r) => r.valid}
      toFormValues={(row) =>
        row
          ? {
              login: row.login,
              pw: "",
              host: row.host,
              account_type: row.account_type,
              queue_id: String(row.queue_id),
              imap_folder: row.imap_folder ?? "INBOX",
              authentication_type: row.authentication_type || "password",
              oauth2_token_config_id: row.oauth2_token_config_id
                ? String(row.oauth2_token_config_id)
                : "",
              trusted: row.trusted,
              comments: row.comments ?? "",
              valid: row.valid,
            }
          : {
              account_type: "IMAPS",
              authentication_type: "password",
              imap_folder: "INBOX",
              valid: true,
              trusted: false,
            }
      }
      toCreateBody={(v: FieldValues): MailAccountCreate => ({
        login: v.login as string,
        pw: (v.pw as string) || undefined,
        host: v.host as string,
        account_type: v.account_type as MailAccountCreate["account_type"],
        queue_id: Number(v.queue_id),
        imap_folder: (v.imap_folder as string) || "INBOX",
        authentication_type: v.authentication_type as MailAccountCreate["authentication_type"],
        oauth2_token_config_id:
          v.authentication_type === "oauth2_token" && v.oauth2_token_config_id
            ? Number(v.oauth2_token_config_id)
            : null,
        trusted: Boolean(v.trusted),
        comments: (v.comments as string) || null,
        valid: Boolean(v.valid ?? true),
      })}
      toUpdateBody={(v: FieldValues): MailAccountUpdate => ({
        login: v.login as string,
        ...(v.pw ? { pw: v.pw as string } : {}),
        host: v.host as string,
        account_type: v.account_type as MailAccountUpdate["account_type"],
        queue_id: Number(v.queue_id),
        imap_folder: (v.imap_folder as string) || "INBOX",
        authentication_type: v.authentication_type as MailAccountUpdate["authentication_type"],
        oauth2_token_config_id:
          v.authentication_type === "oauth2_token" && v.oauth2_token_config_id
            ? Number(v.oauth2_token_config_id)
            : null,
        trusted: Boolean(v.trusted),
        comments: (v.comments as string) || null,
        valid: Boolean(v.valid ?? true),
      })}
    />
  );
}
