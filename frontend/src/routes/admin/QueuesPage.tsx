import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { toBcp47 } from "@/i18n";
import { useQuery } from "@tanstack/react-query";
import { api, type QueueOut, type QueueCreate, type QueueUpdate } from "@/lib/api";
import { AdminResourcePage } from "@/components/admin/AdminResourcePage";
import { CRYPTO_STATUS_KEY } from "@/components/admin/cryptoSettingsQuery";
import { EscalationMatrix } from "@/components/admin/EscalationMatrix";
import { activeEscalationStages } from "@/components/admin/escalation";
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

function encryptMode(v: unknown): "off" | "auto" | "required" {
  return v === "auto" || v === "required" ? v : "off";
}

/** Znuny's seeded `follow_up_possible` names → dialog label keys. Unknown
 * (custom) rows keep their raw name. */
const FOLLOW_UP_LABEL_KEY: Record<string, string> = {
  possible: "admin.queues.dialog.followUpReopen",
  reject: "admin.queues.dialog.followUpReject",
  "new ticket": "admin.queues.dialog.followUpNewTicket",
};

/** Znuny business-hour calendars are numbered slots (TimeZone::Calendar1–9);
 * empty means the default calendar. */
const CALENDAR_SLOTS = ["1", "2", "3", "4", "5", "6", "7", "8", "9"];

export function QueuesPage() {
  const { t, i18n } = useTranslation();
  const locale = toBcp47(i18n.language);

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
  // Znuny-format default sign keys (PGP::Detached::<id>, SMIME::Detached::<file>)
  // of every enabled backend; the backend validates the saved value.
  const signKeysQ = useQuery({
    queryKey: ["admin", "crypto", "sign-key-options"],
    queryFn: ({ signal }) => api.adminCrypto.signKeyOptions({}, signal),
    staleTime: 60 * 1000,
  });
  // The email security section only exists while PGP or S/MIME is enabled
  // and set up — with nothing running there is nothing to configure.
  const cryptoStatusQ = useQuery({
    queryKey: CRYPTO_STATUS_KEY,
    queryFn: ({ signal }) => api.adminCrypto.status(signal),
    staleTime: 60 * 1000,
  });
  const cryptoActive = (cryptoStatusQ.data ?? []).some((b) => b.enabled && b.available);
  // Every queue (valid or not) — stored sign keys / calendars Tiqora cannot
  // offer itself must stay selectable.
  const allQueuesQ = useQuery({
    queryKey: ["admin", "queues", "sign-keys"],
    queryFn: () => api.adminQueues.list({ valid: "all", pageSize: 500 }),
    staleTime: 60 * 1000,
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
  const signKeyOptions = useMemo(() => {
    const opts = [
      { value: "", label: t("admin.queues.defaultSignKeyNone") },
      ...(signKeysQ.data ?? []).map((o) => ({ value: o.value, label: o.label })),
    ];
    // Keep a value Tiqora cannot see (backend off here, key only in Znuny)
    // selectable so editing other fields does not silently drop it.
    const known = new Set(opts.map((o) => o.value));
    for (const q of allQueuesQ.data?.items ?? []) {
      const v = q.default_sign_key;
      if (v && !known.has(v)) {
        opts.push({ value: v, label: t("admin.queues.defaultSignKeyUnknown", { value: v }) });
        known.add(v);
      }
    }
    return opts;
  }, [signKeysQ.data, allQueuesQ.data, t]);
  const followUpOptions = useMemo(
    () =>
      (followUpQ.data ?? []).map((f) => {
        const key = FOLLOW_UP_LABEL_KEY[f.name.trim().toLowerCase()];
        return { value: f.id, label: key ? t(key) : f.name };
      }),
    [followUpQ.data, t],
  );
  const calendarOptions = useMemo(() => {
    const opts = [
      { value: "", label: t("admin.queues.dialog.calendarDefault") },
      ...CALENDAR_SLOTS.map((n) => ({ value: n, label: t("admin.queues.dialog.calendarN", { n }) })),
    ];
    // A stored value outside 1–9 (hand-edited in Znuny) must survive a save.
    const known = new Set(opts.map((o) => o.value));
    for (const q of allQueuesQ.data?.items ?? []) {
      const v = q.calendar_name?.trim();
      if (v && !known.has(v)) {
        opts.push({ value: v, label: v });
        known.add(v);
      }
    }
    return opts;
  }, [allQueuesQ.data, t]);

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

  const tabGeneral = t("admin.queues.dialog.tabGeneral");
  const tabMail = t("admin.queues.dialog.tabMail");
  const tabEscalation = t("admin.queues.dialog.tabEscalation");
  const tabSecurity = t("admin.queues.dialog.tabSecurity");
  const yesNo = { on: t("admin.queues.yes"), off: t("admin.queues.no") };
  const encryptHint: Record<string, string> = {
    off: t("admin.queues.dialog.encryptOffHint"),
    auto: t("admin.queues.dialog.encryptAutoHint"),
    required: t("admin.queues.dialog.encryptRequiredHint"),
  };

  const fields: FieldDef[] = [
    // --- Allgemein ---
    {
      name: "general_intro",
      label: "",
      type: "section",
      tab: tabGeneral,
      helpText: t("admin.queues.dialog.introGeneral"),
    },
    { name: "name", label: t("admin.queues.name"), type: "text", required: true, tab: tabGeneral },
    {
      name: "group_id",
      label: t("admin.queues.group"),
      type: "select",
      required: true,
      options: groupOptions,
      tab: tabGeneral,
      helpText: t("admin.queues.dialog.groupHint"),
      help: { title: t("admin.queues.group"), description: t("admin.help.queues.group") },
    },
    {
      name: "unlock_timeout",
      label: t("admin.queues.dialog.unlockTimeout"),
      type: "number",
      unit: t("admin.queues.dialog.unitMinutesShort"),
      tab: tabGeneral,
      helpText: t("admin.queues.dialog.unlockTimeoutHint"),
    },
    {
      name: "valid_id",
      label: t("admin.table.status"),
      type: "switch",
      switchValues: { on: 1, off: 2 },
      switchLabels: { on: t("admin.table.valid"), off: t("admin.table.invalid") },
      tab: tabGeneral,
      help: { title: t("admin.table.status"), description: t("admin.help.common.validId") },
    },
    {
      name: "comments",
      label: t("admin.queues.dialog.comment"),
      type: "textarea",
      rows: 2,
      tab: tabGeneral,
    },

    // --- Mails & Antworten ---
    {
      name: "mail_intro",
      label: "",
      type: "section",
      tab: tabMail,
      helpText: t("admin.queues.dialog.introMail"),
    },
    {
      name: "system_address_id",
      label: t("admin.queues.dialog.sender"),
      type: "select",
      required: true,
      options: systemAddressOptions,
      tab: tabMail,
      help: {
        title: t("admin.queues.dialog.sender"),
        description: t("admin.help.queues.systemAddress"),
      },
    },
    {
      name: "salutation_id",
      label: t("admin.queues.salutation"),
      type: "select",
      required: true,
      options: salutationOptions,
      width: "half",
      tab: tabMail,
    },
    {
      name: "signature_id",
      label: t("admin.queues.signature"),
      type: "select",
      required: true,
      options: signatureOptions,
      width: "half",
      tab: tabMail,
    },
    {
      name: "follow_up_section",
      label: t("admin.queues.dialog.followUpSection"),
      type: "section",
      tab: tabMail,
      helpText: t("admin.queues.dialog.followUpIntro"),
    },
    {
      name: "follow_up_id",
      label: t("admin.queues.dialog.followUp"),
      type: "segmented",
      required: true,
      options: followUpOptions,
      tab: tabMail,
      help: { title: t("admin.queues.followUp"), description: t("admin.help.queues.followUp") },
    },
    {
      name: "follow_up_lock",
      label: t("admin.queues.dialog.followUpLock"),
      type: "switch",
      switchValues: { on: 1, off: 0 },
      switchLabels: yesNo,
      tab: tabMail,
      helpText: t("admin.queues.dialog.followUpLockHint"),
      help: {
        title: t("admin.queues.dialog.followUpLock"),
        description: t("admin.help.queues.followUpLock"),
      },
    },

    // --- Eskalation ---
    {
      name: "escalation_intro",
      label: "",
      type: "section",
      tab: tabEscalation,
      helpText: t("admin.queues.dialog.introEscalation"),
    },
    {
      // Edits the six first_response/update/solution _time/_notify fields;
      // its own value is never sent.
      name: "escalation_matrix",
      label: "",
      type: "custom",
      layout: "block",
      tab: tabEscalation,
      render: (_value, _onChange, values, ctx) => (
        <EscalationMatrix values={values} onChange={ctx.setValue} idFor={ctx.idFor} />
      ),
    },
    {
      name: "calendar_name",
      label: t("admin.queues.dialog.calendar"),
      type: "select",
      options: calendarOptions,
      rowTone: "subtle",
      tab: tabEscalation,
      helpText: t("admin.queues.dialog.calendarHint"),
      help: {
        title: t("admin.queues.dialog.calendar"),
        description: t("admin.help.queues.calendarName"),
      },
    },

    // --- E-Mail-Sicherheit (only while PGP or S/MIME runs) ---
    {
      name: "email_security_section",
      label: "",
      type: "section",
      tab: tabSecurity,
      helpText: t("admin.queues.emailSecurityHint"),
      showIf: () => cryptoActive,
    },
    {
      name: "default_sign_key",
      label: t("admin.queues.dialog.signKey"),
      type: "select",
      options: signKeyOptions,
      tab: tabSecurity,
      showIf: () => cryptoActive,
      help: {
        title: t("admin.queues.dialog.signKey"),
        description: t("admin.help.queues.defaultSignKey"),
      },
    },
    {
      name: "email_sign_default",
      label: t("admin.queues.emailSignDefault"),
      type: "switch",
      switchLabels: yesNo,
      tab: tabSecurity,
      showIf: (v) => cryptoActive && Boolean(v.default_sign_key),
      help: {
        title: t("admin.queues.emailSignDefault"),
        description: t("admin.help.queues.emailSignDefault"),
      },
    },
    {
      name: "email_encrypt",
      label: t("admin.queues.dialog.encrypt"),
      type: "segmented",
      options: [
        { value: "off", label: t("admin.queues.dialog.encryptOff") },
        { value: "auto", label: t("admin.queues.dialog.encryptAuto") },
        { value: "required", label: t("admin.queues.dialog.encryptRequired") },
      ],
      tab: tabSecurity,
      helpText: (v) => encryptHint[encryptMode(v.email_encrypt)],
      showIf: () => cryptoActive,
      help: {
        title: t("admin.queues.dialog.encrypt"),
        description: t("admin.help.queues.emailEncrypt"),
      },
    },
  ];

  return (
    <AdminResourcePage
      resourceKey="queues"
      title={t("admin.queues.title_plural")}
      newLabel={t("admin.queues.new")}
      editTitle={(row) => t("admin.queues.dialog.editTitle", { name: row.name })}
      api={api.adminQueues}
      idOf={(r) => r.id}
      columns={columns}
      fields={fields}
      drawer={{
        size: "xl",
        appearance: "settings",
        stableTabHeight: true,
        footerStatus: (v) => {
          const count = activeEscalationStages(v);
          return count > 0
            ? t("admin.queues.dialog.escalationActive", { count })
            : t("admin.queues.dialog.escalationNone");
        },
      }}
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
              email_sign_default: row.email_sign_default ?? true,
              email_encrypt: row.email_encrypt ?? "off",
              comments: row.comments ?? "",
              valid_id: row.valid_id,
            }
          : { follow_up_lock: 0, valid_id: 1, email_sign_default: true, email_encrypt: "off" }
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
        email_sign_default: v.email_sign_default !== false,
        email_encrypt: encryptMode(v.email_encrypt),
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
        email_sign_default: v.email_sign_default !== false,
        email_encrypt: encryptMode(v.email_encrypt),
        comments: emptyToNullStr(v.comments),
        valid_id: Number(v.valid_id) || 1,
      })}
    />
  );
}
