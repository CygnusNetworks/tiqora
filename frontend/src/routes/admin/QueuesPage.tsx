import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { api, type QueueOut, type QueueCreate, type QueueUpdate } from "@/lib/api";
import { AdminResourcePage } from "@/components/admin/AdminResourcePage";
import type { FieldDef, FieldValues } from "@/components/admin/CrudDrawer";
import type { DataTableColumn } from "@/components/admin/DataTable";
import { formatDateTime } from "@/lib/format";

function emptyToNull(v: unknown): number | null {
  if (v === "" || v === undefined || v === null) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function emptyToNullStr(v: unknown): string | null {
  if (v === undefined || v === null) return null;
  const s = String(v).trim();
  return s === "" ? null : s;
}

export function QueuesPage() {
  const { t, i18n } = useTranslation();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";

  const groupsQ = useQuery({
    queryKey: ["admin", "groups", "ref"],
    queryFn: () => api.adminGroups.list({ valid: "all", pageSize: 500 }),
    staleTime: 5 * 60 * 1000,
  });
  const systemAddressesQ = useQuery({
    queryKey: ["admin", "system-addresses"],
    queryFn: () => api.listSystemAddresses(),
    staleTime: 5 * 60 * 1000,
  });
  const salutationsQ = useQuery({
    queryKey: ["admin", "salutations", "ref"],
    queryFn: () => api.adminSalutations.list({ valid: "valid", pageSize: 500 }),
    staleTime: 5 * 60 * 1000,
  });
  const signaturesQ = useQuery({
    queryKey: ["admin", "signatures", "ref"],
    queryFn: () => api.adminSignatures.list({ valid: "valid", pageSize: 500 }),
    staleTime: 5 * 60 * 1000,
  });
  const followUpQ = useQuery({
    queryKey: ["admin", "follow-up-possible"],
    queryFn: () => api.listFollowUpPossible(),
    staleTime: 5 * 60 * 1000,
  });

  const groupName = (id: number) =>
    groupsQ.data?.items.find((g) => g.id === id)?.name ?? String(id);
  const systemAddressLabel = (id: number) => {
    const sa = systemAddressesQ.data?.find((a) => a.id === id);
    if (!sa) return String(id);
    return sa.value1 ? `${sa.value1} <${sa.value0}>` : sa.value0;
  };
  const validityLabel = (id: number) =>
    id === 1 ? t("admin.table.valid") : t("admin.table.invalid");

  const groupOptions = useMemo(
    () => (groupsQ.data?.items ?? []).map((g) => ({ value: g.id, label: g.name })),
    [groupsQ.data],
  );
  const systemAddressOptions = useMemo(
    () =>
      (systemAddressesQ.data ?? []).map((a) => ({
        value: a.id,
        label: a.value1 ? `${a.value1} <${a.value0}>` : a.value0,
      })),
    [systemAddressesQ.data],
  );
  const salutationOptions = useMemo(
    () => (salutationsQ.data?.items ?? []).map((s) => ({ value: s.id, label: s.name })),
    [salutationsQ.data],
  );
  const signatureOptions = useMemo(
    () => (signaturesQ.data?.items ?? []).map((s) => ({ value: s.id, label: s.name })),
    [signaturesQ.data],
  );
  const followUpOptions = useMemo(
    () => (followUpQ.data ?? []).map((f) => ({ value: f.id, label: f.name })),
    [followUpQ.data],
  );

  const columns: DataTableColumn<QueueOut>[] = [
    { key: "id", header: t("admin.table.id"), mono: true, render: (r) => r.id },
    { key: "name", header: t("admin.queues.name"), render: (r) => r.name },
    { key: "group_id", header: t("admin.queues.group"), render: (r) => groupName(r.group_id) },
    {
      key: "system_address_id",
      header: t("admin.queues.systemAddress"),
      render: (r) => systemAddressLabel(r.system_address_id),
    },
    {
      key: "valid_id",
      header: t("admin.table.status"),
      render: (r) => validityLabel(r.valid_id),
    },
    {
      key: "changed",
      header: t("admin.table.changed"),
      render: (r) => formatDateTime(r.change_time, locale),
    },
  ];

  const fields: FieldDef[] = [
    { name: "name", label: t("admin.queues.name"), type: "text", required: true },
    {
      name: "group_id",
      label: t("admin.queues.group"),
      type: "select",
      required: true,
      options: groupOptions,
      help: { title: t("admin.queues.group"), description: t("admin.help.queues.group") },
    },
    {
      name: "system_address_id",
      label: t("admin.queues.systemAddress"),
      type: "select",
      required: true,
      options: systemAddressOptions,
      help: {
        title: t("admin.queues.systemAddress"),
        description: t("admin.help.queues.systemAddress"),
      },
    },
    {
      name: "salutation_id",
      label: t("admin.queues.salutation"),
      type: "select",
      required: true,
      options: salutationOptions,
    },
    {
      name: "signature_id",
      label: t("admin.queues.signature"),
      type: "select",
      required: true,
      options: signatureOptions,
    },
    {
      name: "follow_up_id",
      label: t("admin.queues.followUp"),
      type: "select",
      required: true,
      options: followUpOptions,
      help: { title: t("admin.queues.followUp"), description: t("admin.help.queues.followUp") },
    },
    {
      name: "follow_up_lock",
      label: t("admin.queues.followUpLock"),
      type: "select",
      options: [
        { value: 0, label: t("admin.queues.no") },
        { value: 1, label: t("admin.queues.yes") },
      ],
      help: {
        title: t("admin.queues.followUpLock"),
        description: t("admin.help.queues.followUpLock"),
      },
    },
    {
      name: "unlock_timeout",
      label: t("admin.queues.unlockTimeout"),
      type: "number",
      helpText: t("admin.queues.unlockTimeoutHelp"),
    },
    {
      name: "first_response_time",
      label: t("admin.queues.firstResponseTime"),
      type: "number",
      helpText: t("admin.queues.escalationTimeHelp"),
    },
    {
      name: "first_response_notify",
      label: t("admin.queues.firstResponseNotify"),
      type: "number",
      helpText: t("admin.queues.notifyHelp"),
    },
    {
      name: "update_time",
      label: t("admin.queues.updateTime"),
      type: "number",
      helpText: t("admin.queues.escalationTimeHelp"),
    },
    {
      name: "update_notify",
      label: t("admin.queues.updateNotify"),
      type: "number",
      helpText: t("admin.queues.notifyHelp"),
    },
    {
      name: "solution_time",
      label: t("admin.queues.solutionTime"),
      type: "number",
      helpText: t("admin.queues.escalationTimeHelp"),
    },
    {
      name: "solution_notify",
      label: t("admin.queues.solutionNotify"),
      type: "number",
      helpText: t("admin.queues.notifyHelp"),
    },
    {
      name: "calendar_name",
      label: t("admin.queues.calendarName"),
      type: "text",
      help: {
        title: t("admin.queues.calendarName"),
        description: t("admin.help.queues.calendarName"),
      },
    },
    {
      name: "default_sign_key",
      label: t("admin.queues.defaultSignKey"),
      type: "text",
      help: {
        title: t("admin.queues.defaultSignKey"),
        description: t("admin.help.queues.defaultSignKey"),
      },
    },
    { name: "comments", label: t("admin.table.comments"), type: "textarea" },
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
    <AdminResourcePage
      resourceKey="queues"
      title={t("admin.queues.title_plural")}
      newLabel={t("admin.queues.new")}
      api={api.adminQueues}
      idOf={(r) => r.id}
      columns={columns}
      fields={fields}
      toFormValues={(row) =>
        row
          ? {
              name: row.name,
              group_id: row.group_id,
              system_address_id: row.system_address_id,
              salutation_id: row.salutation_id,
              signature_id: row.signature_id,
              follow_up_id: row.follow_up_id,
              follow_up_lock: row.follow_up_lock ?? 0,
              unlock_timeout: row.unlock_timeout ?? "",
              first_response_time: row.first_response_time ?? "",
              first_response_notify: row.first_response_notify ?? "",
              update_time: row.update_time ?? "",
              update_notify: row.update_notify ?? "",
              solution_time: row.solution_time ?? "",
              solution_notify: row.solution_notify ?? "",
              calendar_name: row.calendar_name ?? "",
              default_sign_key: row.default_sign_key ?? "",
              comments: row.comments ?? "",
              valid_id: row.valid_id,
            }
          : { follow_up_lock: 0, valid_id: 1 }
      }
      toCreateBody={(v: FieldValues): QueueCreate => ({
        name: v.name as string,
        group_id: Number(v.group_id),
        system_address_id: Number(v.system_address_id),
        salutation_id: Number(v.salutation_id),
        signature_id: Number(v.signature_id),
        follow_up_id: Number(v.follow_up_id),
        follow_up_lock: Number(v.follow_up_lock) || 0,
        unlock_timeout: emptyToNull(v.unlock_timeout),
        first_response_time: emptyToNull(v.first_response_time),
        first_response_notify: emptyToNull(v.first_response_notify),
        update_time: emptyToNull(v.update_time),
        update_notify: emptyToNull(v.update_notify),
        solution_time: emptyToNull(v.solution_time),
        solution_notify: emptyToNull(v.solution_notify),
        calendar_name: emptyToNullStr(v.calendar_name),
        default_sign_key: emptyToNullStr(v.default_sign_key),
        comments: emptyToNullStr(v.comments),
        valid_id: Number(v.valid_id) || 1,
      })}
      toUpdateBody={(v: FieldValues): QueueUpdate => ({
        name: v.name as string,
        group_id: Number(v.group_id),
        system_address_id: Number(v.system_address_id),
        salutation_id: Number(v.salutation_id),
        signature_id: Number(v.signature_id),
        follow_up_id: Number(v.follow_up_id),
        follow_up_lock: Number(v.follow_up_lock) || 0,
        unlock_timeout: emptyToNull(v.unlock_timeout),
        first_response_time: emptyToNull(v.first_response_time),
        first_response_notify: emptyToNull(v.first_response_notify),
        update_time: emptyToNull(v.update_time),
        update_notify: emptyToNull(v.update_notify),
        solution_time: emptyToNull(v.solution_time),
        solution_notify: emptyToNull(v.solution_notify),
        calendar_name: emptyToNullStr(v.calendar_name),
        default_sign_key: emptyToNullStr(v.default_sign_key),
        comments: emptyToNullStr(v.comments),
        valid_id: Number(v.valid_id) || 1,
      })}
    />
  );
}
