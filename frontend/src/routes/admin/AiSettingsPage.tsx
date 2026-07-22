import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/lib/api";
import {
  aiApi,
  type AclFeature,
  type AclSubjectType,
  type AiAclOut,
  type AiSettingsUpdate,
  type OperationMode,
} from "@/lib/aiApi";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { Badge } from "@/components/ui/Badge";
import { DataTable, type DataTableColumn } from "@/components/admin/DataTable";
import { CrudDrawer, type FieldDef, type FieldValues } from "@/components/admin/CrudDrawer";
import { PlusIcon } from "@/components/ui/icons";
import { cn } from "@/lib/cn";

const SETTINGS_KEY = ["admin", "ai", "settings"] as const;
const PROVIDERS_KEY = ["admin", "ai", "providers"] as const;
const MCP_KEY = ["admin", "ai", "mcp-clients"] as const;
const POLICIES_KEY = ["admin", "ai", "queue-policies"] as const;
const ACL_KEY = ["admin", "ai", "acl"] as const;

const ACL_SUBJECT_TYPES: AclSubjectType[] = ["group", "role", "user"];
const ACL_FEATURES: AclFeature[] = ["summary", "auto_reply", "manual_assist", "mcp"];

function toAclFormValues(row: AiAclOut | null): FieldValues {
  return row
    ? {
        subject_type: row.subject_type,
        subject_id: row.subject_id,
        feature: row.feature,
        allowed: row.allowed,
        limit_requests_day: row.limit_requests_day ?? "",
        limit_tokens_day: row.limit_tokens_day ?? "",
        limit_requests_month: row.limit_requests_month ?? "",
      }
    : {
        subject_type: "group",
        subject_id: "",
        feature: "auto_reply",
        allowed: true,
        limit_requests_day: "",
        limit_tokens_day: "",
        limit_requests_month: "",
      };
}

export function AiSettingsPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();

  const [disclosureText, setDisclosureText] = useState("");
  const [globalCap, setGlobalCap] = useState<string>("");
  const [pendingMode, setPendingMode] = useState<OperationMode | null>(null);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);

  const [aclDrawerOpen, setAclDrawerOpen] = useState(false);
  const [editingAcl, setEditingAcl] = useState<AiAclOut | null>(null);
  const [aclFormError, setAclFormError] = useState<string | null>(null);

  const settingsQ = useQuery({
    queryKey: SETTINGS_KEY,
    queryFn: ({ signal }) => aiApi.getSettings(signal),
  });
  const providersQ = useQuery({
    queryKey: PROVIDERS_KEY,
    queryFn: ({ signal }) => aiApi.listProviders(signal),
  });
  const mcpQ = useQuery({
    queryKey: MCP_KEY,
    queryFn: ({ signal }) => aiApi.listMcpClients(signal),
  });
  const policiesQ = useQuery({
    queryKey: POLICIES_KEY,
    queryFn: ({ signal }) => aiApi.listQueuePolicies(signal),
  });
  const aclQ = useQuery({
    queryKey: ACL_KEY,
    queryFn: ({ signal }) => aiApi.listAcl(signal),
  });

  useEffect(() => {
    if (settingsQ.data) {
      setDisclosureText(settingsQ.data.disclosure_default_text);
      setGlobalCap(
        settingsQ.data.global_max_replies_per_hour != null
          ? String(settingsQ.data.global_max_replies_per_hour)
          : "",
      );
    }
  }, [settingsQ.data]);

  const saveM = useMutation({
    mutationFn: (body: AiSettingsUpdate) => aiApi.putSettings(body),
    onSuccess: (data) => {
      qc.setQueryData(SETTINGS_KEY, data);
      setStatusMsg(t("admin.ai.settings.saved"));
    },
    onError: () => setStatusMsg(t("admin.ai.settings.saveError")),
  });

  const saveTextAndCap = () => {
    setStatusMsg(null);
    const capNum = globalCap.trim() ? Number(globalCap) : null;
    saveM.mutate({
      disclosure_default_text: disclosureText,
      global_max_replies_per_hour: capNum != null && Number.isFinite(capNum) ? capNum : null,
    });
  };

  const confirmModeChange = () => {
    if (!pendingMode) return;
    saveM.mutate({ operation_mode: pendingMode });
    setPendingMode(null);
  };

  const invalidateAcl = () => qc.invalidateQueries({ queryKey: ACL_KEY });

  const createAclM = useMutation({
    mutationFn: (values: FieldValues) =>
      aiApi.createAcl({
        subject_type: values.subject_type as AclSubjectType,
        subject_id: Number(values.subject_id),
        feature: values.feature as AclFeature,
        allowed: Boolean(values.allowed),
        limit_requests_day: values.limit_requests_day ? Number(values.limit_requests_day) : null,
        limit_tokens_day: values.limit_tokens_day ? Number(values.limit_tokens_day) : null,
        limit_requests_month: values.limit_requests_month
          ? Number(values.limit_requests_month)
          : null,
      }),
    onSuccess: async () => {
      setAclDrawerOpen(false);
      await invalidateAcl();
    },
  });

  const updateAclM = useMutation({
    mutationFn: ({ id, values }: { id: number; values: FieldValues }) =>
      aiApi.updateAcl(id, {
        subject_type: values.subject_type as AclSubjectType,
        subject_id: Number(values.subject_id),
        feature: values.feature as AclFeature,
        allowed: Boolean(values.allowed),
        limit_requests_day: values.limit_requests_day ? Number(values.limit_requests_day) : null,
        limit_tokens_day: values.limit_tokens_day ? Number(values.limit_tokens_day) : null,
        limit_requests_month: values.limit_requests_month
          ? Number(values.limit_requests_month)
          : null,
      }),
    onSuccess: async () => {
      setAclDrawerOpen(false);
      await invalidateAcl();
    },
  });

  const deleteAclM = useMutation({
    mutationFn: (id: number) => aiApi.deleteAcl(id),
    onSuccess: () => invalidateAcl(),
  });

  const openAclCreate = () => {
    setEditingAcl(null);
    setAclFormError(null);
    setAclDrawerOpen(true);
  };
  const openAclEdit = (row: AiAclOut) => {
    setEditingAcl(row);
    setAclFormError(null);
    setAclDrawerOpen(true);
  };

  const handleAclSubmit = async (values: FieldValues) => {
    setAclFormError(null);
    try {
      if (editingAcl) {
        await updateAclM.mutateAsync({ id: editingAcl.id, values });
      } else {
        await createAclM.mutateAsync(values);
      }
    } catch (err) {
      setAclFormError(err instanceof ApiError ? err.message : t("admin.form.genericError"));
      throw err;
    }
  };

  const aclColumns: DataTableColumn<AiAclOut>[] = [
    {
      key: "subject",
      header: t("admin.ai.acl.subject"),
      render: (r) => `${t(`admin.ai.acl.subjectType.${r.subject_type}`)} #${r.subject_id}`,
    },
    {
      key: "feature",
      header: t("admin.ai.acl.feature"),
      render: (r) => t(`admin.ai.feature.${r.feature}`),
    },
    {
      key: "allowed",
      header: t("admin.ai.acl.allowed"),
      render: (r) => (
        <Badge tone={r.allowed ? "success" : "danger"}>
          {r.allowed ? t("admin.ai.acl.allowedYes") : t("admin.ai.acl.allowedNo")}
        </Badge>
      ),
    },
    {
      key: "limits",
      header: t("admin.ai.acl.limits"),
      mono: true,
      render: (r) =>
        [
          r.limit_requests_day != null ? `${r.limit_requests_day}/d req` : null,
          r.limit_tokens_day != null ? `${r.limit_tokens_day}/d tok` : null,
          r.limit_requests_month != null ? `${r.limit_requests_month}/mo req` : null,
        ]
          .filter(Boolean)
          .join(" · ") || "—",
    },
  ];

  const aclFields: FieldDef[] = [
    {
      name: "subject_type",
      label: t("admin.ai.acl.subjectType.label"),
      type: "select",
      required: true,
      options: ACL_SUBJECT_TYPES.map((v) => ({ value: v, label: t(`admin.ai.acl.subjectType.${v}`) })),
    },
    {
      name: "subject_id",
      label: t("admin.ai.acl.subjectId"),
      type: "number",
      required: true,
      helpText: t("admin.ai.acl.subjectIdHelp"),
    },
    {
      name: "feature",
      label: t("admin.ai.acl.feature"),
      type: "select",
      required: true,
      options: ACL_FEATURES.map((v) => ({ value: v, label: t(`admin.ai.feature.${v}`) })),
    },
    { name: "allowed", label: t("admin.ai.acl.allowed"), type: "checkbox" },
    { name: "limit_requests_day", label: t("admin.ai.acl.limitRequestsDay"), type: "number" },
    { name: "limit_tokens_day", label: t("admin.ai.acl.limitTokensDay"), type: "number" },
    { name: "limit_requests_month", label: t("admin.ai.acl.limitRequestsMonth"), type: "number" },
  ];

  if (settingsQ.isLoading) {
    return (
      <div className="flex items-center gap-2 p-4" data-testid="admin-ai-settings-page">
        <Spinner />
      </div>
    );
  }

  if (settingsQ.isError || !settingsQ.data) {
    return (
      <div className="p-4 text-sm text-danger" data-testid="admin-ai-settings-page">
        {t("admin.ai.settings.loadError")}
      </div>
    );
  }

  const mode = settingsQ.data.operation_mode;
  const activePolicyCount = (policiesQ.data?.items ?? []).filter(
    (p) => p.enabled_auto_reply || p.enabled_summary || p.enabled_manual_assist,
  ).length;

  return (
    <div className="mx-auto max-w-3xl space-y-4 p-4" data-testid="admin-ai-settings-page">
      <div>
        <h1 className="font-display text-xl font-semibold text-ink">{t("admin.ai.settings.title")}</h1>
        <p className="mt-1 text-sm text-muted">{t("admin.ai.settings.description")}</p>
      </div>

      {mode === "parallel" && (
        <div
          className="rounded-lg border border-escalation/40 bg-escalation/10 p-3 text-sm text-escalation"
          data-testid="admin-ai-parallel-banner"
        >
          {t("admin.ai.settings.parallelBanner")}
        </div>
      )}

      <div className="grid grid-cols-3 gap-3">
        <div className="rounded-lg border border-hairline bg-surface p-3">
          <p className="text-xs uppercase tracking-wide text-muted">
            {t("admin.ai.settings.statProviders")}
          </p>
          <p className="mt-1 font-mono text-2xl text-ink" data-testid="admin-ai-stat-providers">
            {providersQ.data?.total ?? "—"}
          </p>
        </div>
        <div className="rounded-lg border border-hairline bg-surface p-3">
          <p className="text-xs uppercase tracking-wide text-muted">{t("admin.ai.settings.statMcp")}</p>
          <p className="mt-1 font-mono text-2xl text-ink" data-testid="admin-ai-stat-mcp">
            {mcpQ.data?.total ?? "—"}
          </p>
        </div>
        <div className="rounded-lg border border-hairline bg-surface p-3">
          <p className="text-xs uppercase tracking-wide text-muted">
            {t("admin.ai.settings.statActivePolicies")}
          </p>
          <p className="mt-1 font-mono text-2xl text-ink" data-testid="admin-ai-stat-policies">
            {policiesQ.data ? activePolicyCount : "—"}
          </p>
        </div>
      </div>

      <div className="space-y-3 rounded-lg border border-hairline bg-surface p-4">
        <h2 className="font-display text-sm font-semibold text-ink">
          {t("admin.ai.settings.operationMode")}
        </h2>
        <p className="text-xs text-muted">{t("admin.ai.settings.operationModeHint")}</p>
        <div
          className="inline-flex rounded-lg border border-hairline bg-surface p-0.5"
          role="group"
          aria-label={t("admin.ai.settings.operationMode")}
        >
          {(["parallel", "tiqora_primary"] as OperationMode[]).map((m) => (
            <button
              key={m}
              type="button"
              aria-pressed={mode === m}
              data-testid={`admin-ai-mode-${m}`}
              disabled={saveM.isPending}
              onClick={() => {
                if (m !== mode) setPendingMode(m);
              }}
              className={cn(
                "rounded-md px-3 py-1 text-xs font-medium transition-colors",
                mode === m
                  ? "bg-accent text-white"
                  : "text-muted hover:bg-surface-subtle hover:text-ink",
              )}
            >
              {t(`admin.ai.settings.mode.${m}`)}
            </button>
          ))}
        </div>

        {pendingMode && (
          <div
            className="space-y-2 rounded-md border border-escalation/40 bg-escalation/10 p-3"
            data-testid="admin-ai-mode-confirm"
          >
            <p className="text-sm text-ink">
              {t("admin.ai.settings.modeConfirm", { mode: t(`admin.ai.settings.mode.${pendingMode}`) })}
            </p>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={() => setPendingMode(null)}>
                {t("admin.form.cancel")}
              </Button>
              <Button
                variant="primary"
                size="sm"
                data-testid="admin-ai-mode-confirm-apply"
                onClick={confirmModeChange}
              >
                {t("admin.ai.settings.modeConfirmApply")}
              </Button>
            </div>
          </div>
        )}
      </div>

      <div className="space-y-3 rounded-lg border border-hairline bg-surface p-4">
        <h2 className="font-display text-sm font-semibold text-ink">
          {t("admin.ai.settings.disclosure")}
        </h2>
        <label className="block text-sm">
          <span className="mb-1 block text-muted">{t("admin.ai.settings.disclosureText")}</span>
          <textarea
            data-testid="admin-ai-disclosure-text"
            value={disclosureText}
            onChange={(e) => setDisclosureText(e.target.value)}
            rows={3}
            className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink"
          />
        </label>
        <label className="block text-sm sm:w-64">
          <span className="mb-1 block text-muted">{t("admin.ai.settings.globalCap")}</span>
          <input
            data-testid="admin-ai-global-cap"
            type="number"
            min={0}
            placeholder={t("admin.ai.settings.globalCapPlaceholder")}
            value={globalCap}
            onChange={(e) => setGlobalCap(e.target.value)}
            className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink"
          />
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="primary"
            size="sm"
            data-testid="admin-ai-settings-save"
            disabled={saveM.isPending}
            onClick={saveTextAndCap}
          >
            {saveM.isPending ? t("admin.form.saving") : t("admin.form.save")}
          </Button>
          {statusMsg && (
            <span className="text-sm text-muted" data-testid="admin-ai-settings-status">
              {statusMsg}
            </span>
          )}
        </div>
      </div>

      <div className="space-y-3 rounded-lg border border-hairline bg-surface p-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h2 className="font-display text-sm font-semibold text-ink">{t("admin.ai.acl.title")}</h2>
            <p className="text-xs text-muted">{t("admin.ai.acl.description")}</p>
          </div>
          <Button
            variant="primary"
            size="sm"
            data-testid="admin-ai-acl-new"
            onClick={openAclCreate}
            aria-label={t("admin.ai.acl.new")}
            title={t("admin.ai.acl.new")}
            className="!px-2"
          >
            <PlusIcon className="text-[16px]" />
          </Button>
        </div>
        <DataTable
          columns={aclColumns}
          rows={aclQ.data ?? []}
          rowKey={(r) => r.id}
          isLoading={aclQ.isLoading}
          onEdit={openAclEdit}
          onDelete={(row) => deleteAclM.mutate(row.id)}
          testId="admin-ai-acl-table"
        />
      </div>

      <CrudDrawer
        open={aclDrawerOpen}
        onClose={() => setAclDrawerOpen(false)}
        title={editingAcl ? t("admin.form.editTitle", { title: t("admin.ai.acl.title") }) : t("admin.ai.acl.new")}
        fields={aclFields}
        mode={editingAcl ? "edit" : "create"}
        initialValues={toAclFormValues(editingAcl)}
        onSubmit={handleAclSubmit}
        submitError={aclFormError}
        testIdPrefix="admin-ai-acl-form"
      />
    </div>
  );
}
