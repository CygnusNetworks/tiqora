import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, ApiError, type QueueRef } from "@/lib/api";
import {
  aiApi,
  type AclFeature,
  type AiQueuePolicyCreate,
  type AiQueuePolicyOut,
  type AiQueuePolicyUpdate,
  type Autonomy,
  type IdentityMode,
} from "@/lib/aiApi";
import { DataTable, type DataTableColumn } from "@/components/admin/DataTable";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Spinner } from "@/components/ui/Spinner";
import { PlusIcon } from "@/components/ui/icons";
import { formatDateTime } from "@/lib/format";

const POLICIES_KEY = ["admin", "ai", "queue-policies"] as const;
const QUEUES_KEY = ["admin", "ai", "reference-queues"] as const;
const PROVIDERS_KEY = ["admin", "ai", "providers"] as const;
const MCP_KEY = ["admin", "ai", "mcp-clients"] as const;
const AGENTS_KEY = ["admin", "ai", "reference-agents"] as const;
const USAGE_KEY = ["admin", "ai", "usage"] as const;

const AUTONOMY_VALUES: Autonomy[] = ["off", "clarify_only", "full"];
const IDENTITY_MODES: IdentityMode[] = ["ticket_customer_id", "clarify_schema", "off"];
const FEATURES: AclFeature[] = ["summary", "auto_reply", "manual_assist"];

type FormState = {
  enabled_auto_reply: boolean;
  enabled_summary: boolean;
  enabled_manual_assist: boolean;
  autonomy: Autonomy;
  system_prompt: string;
  llm_provider_id: string;
  model_override: string;
  service_user_id: string;
  kb_tags: string;
  kb_category_ids: string;
  mcp_client_ids: Set<number>;
  summary_article_threshold: string;
  summary_char_threshold: string;
  summary_incremental_min_articles: string;
  summary_incremental_min_chars: string;
  max_clarifications: string;
  max_auto_replies: string;
  max_replies_per_hour: string;
  budget_tokens_day: string;
  escalation_rules: string;
  ai_disclosure_enabled: boolean;
  ai_disclosure_text: string;
  pii_masking: boolean;
  identity_mode: IdentityMode;
  clarify_schema_json: string;
};

function emptyForm(): FormState {
  return {
    enabled_auto_reply: false,
    enabled_summary: false,
    enabled_manual_assist: false,
    autonomy: "off",
    system_prompt: "",
    llm_provider_id: "",
    model_override: "",
    service_user_id: "",
    kb_tags: "",
    kb_category_ids: "",
    mcp_client_ids: new Set(),
    summary_article_threshold: "",
    summary_char_threshold: "",
    summary_incremental_min_articles: "",
    summary_incremental_min_chars: "",
    max_clarifications: "2",
    max_auto_replies: "5",
    max_replies_per_hour: "",
    budget_tokens_day: "",
    escalation_rules: "",
    ai_disclosure_enabled: false,
    ai_disclosure_text: "",
    pii_masking: true,
    identity_mode: "ticket_customer_id",
    clarify_schema_json: "",
  };
}

function toForm(row: AiQueuePolicyOut): FormState {
  return {
    enabled_auto_reply: row.enabled_auto_reply,
    enabled_summary: row.enabled_summary,
    enabled_manual_assist: row.enabled_manual_assist,
    autonomy: row.autonomy,
    system_prompt: row.system_prompt,
    llm_provider_id: row.llm_provider_id != null ? String(row.llm_provider_id) : "",
    model_override: row.model_override ?? "",
    service_user_id: row.service_user_id != null ? String(row.service_user_id) : "",
    kb_tags: row.kb_tags ?? "",
    kb_category_ids: row.kb_category_ids ?? "",
    mcp_client_ids: new Set(
      (row.mcp_client_ids ?? "")
        .split(",")
        .map((s) => Number(s.trim()))
        .filter((n) => Number.isFinite(n)),
    ),
    summary_article_threshold: row.summary_article_threshold != null ? String(row.summary_article_threshold) : "",
    summary_char_threshold: row.summary_char_threshold != null ? String(row.summary_char_threshold) : "",
    summary_incremental_min_articles:
      row.summary_incremental_min_articles != null ? String(row.summary_incremental_min_articles) : "",
    summary_incremental_min_chars:
      row.summary_incremental_min_chars != null ? String(row.summary_incremental_min_chars) : "",
    max_clarifications: String(row.max_clarifications),
    max_auto_replies: String(row.max_auto_replies),
    max_replies_per_hour: row.max_replies_per_hour != null ? String(row.max_replies_per_hour) : "",
    budget_tokens_day: row.budget_tokens_day != null ? String(row.budget_tokens_day) : "",
    escalation_rules: row.escalation_rules ?? "",
    ai_disclosure_enabled: row.ai_disclosure_enabled,
    ai_disclosure_text: row.ai_disclosure_text ?? "",
    pii_masking: row.pii_masking,
    identity_mode: row.identity_mode,
    clarify_schema_json: row.clarify_schema_json ?? "",
  };
}

function numOrNull(v: string): number | null {
  const trimmed = v.trim();
  if (!trimmed) return null;
  const n = Number(trimmed);
  return Number.isFinite(n) ? n : null;
}

function validateJson(
  label: string,
  value: string,
  t: (k: string, o?: Record<string, unknown>) => string,
): string | null {
  if (!value.trim()) return null;
  try {
    JSON.parse(value);
    return null;
  } catch {
    return t("admin.ai.queues.invalidJson", { field: label });
  }
}

export function AiQueuePoliciesPage() {
  const { t, i18n } = useTranslation();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";
  const qc = useQueryClient();

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<AiQueuePolicyOut | null>(null);
  const [newQueueId, setNewQueueId] = useState<string>("");
  const [form, setForm] = useState<FormState>(emptyForm());
  const [formError, setFormError] = useState<string | null>(null);
  const [jsonErrors, setJsonErrors] = useState<{ escalation?: string; clarify?: string }>({});

  const [usageQueueFilter, setUsageQueueFilter] = useState<string>("");
  const [usageFeatureFilter, setUsageFeatureFilter] = useState<string>("");
  const [usagePage, setUsagePage] = useState(1);

  const policiesQ = useQuery({
    queryKey: POLICIES_KEY,
    queryFn: ({ signal }) => aiApi.listQueuePolicies(signal),
  });
  const queuesQ = useQuery({
    queryKey: QUEUES_KEY,
    queryFn: ({ signal }) => api.listReferenceQueues({}, signal),
  });
  const providersQ = useQuery({
    queryKey: PROVIDERS_KEY,
    queryFn: ({ signal }) => aiApi.listProviders(signal),
  });
  const mcpQ = useQuery({
    queryKey: MCP_KEY,
    queryFn: ({ signal }) => aiApi.listMcpClients(signal),
  });
  const agentsQ = useQuery({
    queryKey: AGENTS_KEY,
    queryFn: ({ signal }) => api.listReferenceAgents(signal),
  });

  const usageQ = useQuery({
    queryKey: [...USAGE_KEY, usageQueueFilter, usageFeatureFilter, usagePage],
    queryFn: ({ signal }) =>
      aiApi.listUsage(
        {
          queue_id: usageQueueFilter ? Number(usageQueueFilter) : undefined,
          feature: usageFeatureFilter ? (usageFeatureFilter as AclFeature) : undefined,
          page: usagePage,
          page_size: 25,
        },
        signal,
      ),
  });

  const queueNameById = useMemo(() => {
    const map = new Map<number, string>();
    for (const q of queuesQ.data ?? []) map.set(q.id, q.name);
    return map;
  }, [queuesQ.data]);

  const availableQueues: QueueRef[] = useMemo(() => {
    const used = new Set((policiesQ.data?.items ?? []).map((p) => p.queue_id));
    return (queuesQ.data ?? []).filter((q) => !used.has(q.id));
  }, [queuesQ.data, policiesQ.data]);

  const invalidate = () => qc.invalidateQueries({ queryKey: POLICIES_KEY });

  const createM = useMutation({
    mutationFn: (body: AiQueuePolicyCreate) => aiApi.createQueuePolicy(body),
    onSuccess: async () => {
      setDialogOpen(false);
      await invalidate();
    },
  });
  const updateM = useMutation({
    mutationFn: ({ id, body }: { id: number; body: AiQueuePolicyUpdate }) =>
      aiApi.updateQueuePolicy(id, body),
    onSuccess: async () => {
      setDialogOpen(false);
      await invalidate();
    },
  });
  const deleteM = useMutation({
    mutationFn: (id: number) => aiApi.deleteQueuePolicy(id),
    onSuccess: () => invalidate(),
  });

  const openCreate = () => {
    setEditing(null);
    setNewQueueId(availableQueues[0] ? String(availableQueues[0].id) : "");
    setForm(emptyForm());
    setFormError(null);
    setJsonErrors({});
    setDialogOpen(true);
  };
  const openEdit = (row: AiQueuePolicyOut) => {
    setEditing(row);
    setForm(toForm(row));
    setFormError(null);
    setJsonErrors({});
    setDialogOpen(true);
  };

  const setField = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  const toggleMcpClient = (id: number) => {
    setForm((f) => {
      const next = new Set(f.mcp_client_ids);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return { ...f, mcp_client_ids: next };
    });
  };

  const buildBody = (): AiQueuePolicyCreate | AiQueuePolicyUpdate => ({
    enabled_auto_reply: form.enabled_auto_reply,
    enabled_summary: form.enabled_summary,
    enabled_manual_assist: form.enabled_manual_assist,
    autonomy: form.autonomy,
    system_prompt: form.system_prompt,
    llm_provider_id: form.llm_provider_id ? Number(form.llm_provider_id) : null,
    model_override: form.model_override.trim() || null,
    service_user_id: form.service_user_id ? Number(form.service_user_id) : null,
    kb_tags: form.kb_tags.trim() || null,
    kb_category_ids: form.kb_category_ids.trim() || null,
    mcp_client_ids: form.mcp_client_ids.size > 0 ? Array.from(form.mcp_client_ids).join(",") : null,
    summary_article_threshold: numOrNull(form.summary_article_threshold),
    summary_char_threshold: numOrNull(form.summary_char_threshold),
    summary_incremental_min_articles: numOrNull(form.summary_incremental_min_articles),
    summary_incremental_min_chars: numOrNull(form.summary_incremental_min_chars),
    max_clarifications: numOrNull(form.max_clarifications) ?? 2,
    max_auto_replies: numOrNull(form.max_auto_replies) ?? 5,
    max_replies_per_hour: numOrNull(form.max_replies_per_hour),
    budget_tokens_day: numOrNull(form.budget_tokens_day),
    escalation_rules: form.escalation_rules.trim() || null,
    ai_disclosure_enabled: form.ai_disclosure_enabled,
    ai_disclosure_text: form.ai_disclosure_text.trim() || null,
    pii_masking: form.pii_masking,
    identity_mode: form.identity_mode,
    clarify_schema_json: form.clarify_schema_json.trim() || null,
  });

  const handleSubmit = async () => {
    setFormError(null);
    const escalationErr = validateJson("escalation_rules", form.escalation_rules, t);
    const clarifyErr = validateJson("clarify_schema_json", form.clarify_schema_json, t);
    setJsonErrors({ escalation: escalationErr ?? undefined, clarify: clarifyErr ?? undefined });
    if (escalationErr || clarifyErr) return;

    try {
      if (editing) {
        await updateM.mutateAsync({ id: editing.id, body: buildBody() });
      } else {
        const queueId = Number(newQueueId);
        if (!Number.isFinite(queueId) || queueId <= 0) {
          setFormError(t("admin.ai.queues.queueRequired"));
          return;
        }
        await createM.mutateAsync({ queue_id: queueId, ...buildBody() });
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setFormError(t("admin.ai.queues.gateError"));
      } else {
        setFormError(err instanceof ApiError ? err.message : t("admin.form.genericError"));
      }
    }
  };

  const providerName = (id: number | null) => {
    if (id == null) return "—";
    return providersQ.data?.items.find((p) => p.id === id)?.name ?? `#${id}`;
  };

  const columns: DataTableColumn<AiQueuePolicyOut>[] = [
    {
      key: "queue",
      header: t("admin.ai.queues.queue"),
      render: (r) => queueNameById.get(r.queue_id) ?? `#${r.queue_id}`,
    },
    {
      key: "features",
      header: t("admin.ai.queues.features"),
      render: (r) => (
        <div className="flex flex-wrap gap-1">
          {r.enabled_manual_assist && <Badge tone="accent">{t("admin.ai.feature.manual_assist")}</Badge>}
          {r.enabled_summary && <Badge tone="accent">{t("admin.ai.feature.summary")}</Badge>}
          {r.enabled_auto_reply && <Badge tone="warn">{t("admin.ai.feature.auto_reply")}</Badge>}
          {!r.enabled_manual_assist && !r.enabled_summary && !r.enabled_auto_reply && (
            <span className="text-muted">—</span>
          )}
        </div>
      ),
    },
    {
      key: "autonomy",
      header: t("admin.ai.queues.autonomy.label"),
      render: (r) => t(`admin.ai.queues.autonomy.${r.autonomy}`),
    },
    {
      key: "provider",
      header: t("admin.ai.providers.title"),
      render: (r) => providerName(r.llm_provider_id),
    },
  ];

  return (
    <div className="space-y-6 p-4" data-testid="admin-ai-queues-page">
      <div>
        <div className="flex items-center justify-between gap-3">
          <h1 className="font-display text-xl font-semibold text-ink">{t("admin.ai.queues.title")}</h1>
          <Button
            variant="primary"
            size="sm"
            data-testid="admin-ai-queues-new"
            disabled={availableQueues.length === 0}
            onClick={openCreate}
            aria-label={t("admin.ai.queues.new")}
            title={t("admin.ai.queues.new")}
            className="!px-2"
          >
            <PlusIcon className="text-[16px]" />
          </Button>
        </div>
        <p className="mt-1 text-xs text-muted">{t("admin.ai.queues.description")}</p>
      </div>

      <DataTable
        columns={columns}
        rows={policiesQ.data?.items ?? []}
        rowKey={(r) => r.id}
        isLoading={policiesQ.isLoading}
        isRowValid={(r) => r.valid_id === 1}
        onEdit={openEdit}
        onDelete={(row) => {
          if (window.confirm(t("admin.ai.queues.deleteConfirm"))) deleteM.mutate(row.id);
        }}
        testId="admin-ai-queues-table"
      />

      <div className="space-y-3 rounded-lg border border-hairline bg-surface p-4">
        <h2 className="font-display text-sm font-semibold text-ink">{t("admin.ai.usage.title")}</h2>
        <div className="flex flex-wrap items-center gap-2">
          <select
            data-testid="admin-ai-usage-queue-filter"
            value={usageQueueFilter}
            onChange={(e) => {
              setUsageQueueFilter(e.target.value);
              setUsagePage(1);
            }}
            className="rounded-md border border-hairline bg-surface-subtle px-2 py-1 text-xs text-ink"
          >
            <option value="">{t("admin.ai.usage.allQueues")}</option>
            {(queuesQ.data ?? []).map((q) => (
              <option key={q.id} value={q.id}>
                {q.name}
              </option>
            ))}
          </select>
          <select
            data-testid="admin-ai-usage-feature-filter"
            value={usageFeatureFilter}
            onChange={(e) => {
              setUsageFeatureFilter(e.target.value);
              setUsagePage(1);
            }}
            className="rounded-md border border-hairline bg-surface-subtle px-2 py-1 text-xs text-ink"
          >
            <option value="">{t("admin.ai.usage.allFeatures")}</option>
            {[...FEATURES, "mcp" as AclFeature].map((f) => (
              <option key={f} value={f}>
                {t(`admin.ai.feature.${f}`)}
              </option>
            ))}
          </select>
          {usageQ.data && (
            <span className="text-xs text-muted" data-testid="admin-ai-usage-totals">
              {t("admin.ai.usage.totals", {
                prompt: usageQ.data.total_prompt_tokens,
                completion: usageQ.data.total_completion_tokens,
              })}
            </span>
          )}
        </div>
        {usageQ.isLoading ? (
          <Spinner />
        ) : (
          <div className="overflow-x-auto rounded-lg border border-hairline">
            <table className="w-full min-w-[640px] text-left text-xs" data-testid="admin-ai-usage-table">
              <thead className="border-b border-hairline bg-surface-subtle uppercase tracking-wide text-muted">
                <tr>
                  <th className="px-2 py-1.5">{t("admin.ai.usage.ts")}</th>
                  <th className="px-2 py-1.5">{t("admin.ai.usage.queue")}</th>
                  <th className="px-2 py-1.5">{t("admin.ai.usage.feature")}</th>
                  <th className="px-2 py-1.5">{t("admin.ai.usage.model")}</th>
                  <th className="px-2 py-1.5">{t("admin.ai.usage.tokens")}</th>
                  <th className="px-2 py-1.5">{t("admin.ai.usage.success")}</th>
                </tr>
              </thead>
              <tbody>
                {(usageQ.data?.items ?? []).map((u) => (
                  <tr key={u.id} className="border-b border-hairline last:border-0">
                    <td className="px-2 py-1">{formatDateTime(u.ts, locale)}</td>
                    <td className="px-2 py-1">{u.queue_id != null ? queueNameById.get(u.queue_id) ?? u.queue_id : "—"}</td>
                    <td className="px-2 py-1">{t(`admin.ai.feature.${u.feature}`)}</td>
                    <td className="px-2 py-1 font-mono">{u.model ?? "—"}</td>
                    <td className="px-2 py-1 font-mono">{u.prompt_tokens}/{u.completion_tokens}</td>
                    <td className="px-2 py-1">
                      <Badge tone={u.success ? "success" : "danger"}>
                        {u.success ? t("admin.ai.usage.ok") : t("admin.ai.usage.failed")}
                      </Badge>
                    </td>
                  </tr>
                ))}
                {(usageQ.data?.items.length ?? 0) === 0 && (
                  <tr>
                    <td colSpan={6} className="px-2 py-4 text-center text-muted">
                      {t("admin.table.empty")}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <Dialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        title={
          editing
            ? t("admin.form.editTitle", {
                title: queueNameById.get(editing.queue_id) ?? `#${editing.queue_id}`,
              })
            : t("admin.ai.queues.new")
        }
        className="max-w-3xl"
      >
        <div
          className="flex max-h-[75vh] flex-col gap-4 overflow-y-auto"
          data-testid="admin-ai-queue-form"
        >
          {!editing && (
            <label className="block text-sm">
              <span className="mb-1 block text-muted">{t("admin.ai.queues.queue")}</span>
              <select
                data-testid="admin-ai-queue-form-queue_id"
                value={newQueueId}
                onChange={(e) => setNewQueueId(e.target.value)}
                className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink"
              >
                {availableQueues.map((q) => (
                  <option key={q.id} value={q.id}>
                    {q.name}
                  </option>
                ))}
              </select>
            </label>
          )}

          <section className="space-y-2">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">
              {t("admin.ai.queues.section.features")}
            </h3>
            <label className="flex items-center gap-2 text-sm text-ink">
              <input
                type="checkbox"
                data-testid="admin-ai-queue-form-enabled_manual_assist"
                checked={form.enabled_manual_assist}
                onChange={(e) => setField("enabled_manual_assist", e.target.checked)}
                className="rounded border-hairline"
              />
              {t("admin.ai.feature.manual_assist")}
            </label>
            <label className="flex items-center gap-2 text-sm text-ink">
              <input
                type="checkbox"
                data-testid="admin-ai-queue-form-enabled_summary"
                checked={form.enabled_summary}
                onChange={(e) => setField("enabled_summary", e.target.checked)}
                className="rounded border-hairline"
              />
              {t("admin.ai.feature.summary")}
            </label>
            <label className="flex items-center gap-2 text-sm text-ink">
              <input
                type="checkbox"
                data-testid="admin-ai-queue-form-enabled_auto_reply"
                checked={form.enabled_auto_reply}
                onChange={(e) => setField("enabled_auto_reply", e.target.checked)}
                className="rounded border-hairline"
              />
              {t("admin.ai.feature.auto_reply")}
            </label>
          </section>

          <section className="space-y-2">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">
              {t("admin.ai.queues.section.autonomy")}
            </h3>
            <select
              data-testid="admin-ai-queue-form-autonomy"
              value={form.autonomy}
              onChange={(e) => setField("autonomy", e.target.value as Autonomy)}
              className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink"
            >
              {AUTONOMY_VALUES.map((a) => (
                <option key={a} value={a}>
                  {t(`admin.ai.queues.autonomy.${a}`)}
                </option>
              ))}
            </select>
            <p className="text-xs text-muted">{t(`admin.ai.queues.autonomy.${form.autonomy}Hint`)}</p>
          </section>

          <section className="space-y-2">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">
              {t("admin.ai.queues.section.prompt")}
            </h3>
            <textarea
              data-testid="admin-ai-queue-form-system_prompt"
              value={form.system_prompt}
              onChange={(e) => setField("system_prompt", e.target.value)}
              rows={4}
              className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink"
            />
          </section>

          <section className="grid gap-3 sm:grid-cols-2">
            <label className="block text-sm">
              <span className="mb-1 block text-muted">{t("admin.ai.providers.title")}</span>
              <select
                data-testid="admin-ai-queue-form-llm_provider_id"
                value={form.llm_provider_id}
                onChange={(e) => setField("llm_provider_id", e.target.value)}
                className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink"
              >
                <option value="">{t("admin.form.selectPlaceholder")}</option>
                {(providersQ.data?.items ?? []).map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-sm">
              <span className="mb-1 block text-muted">{t("admin.ai.queues.modelOverride")}</span>
              <input
                data-testid="admin-ai-queue-form-model_override"
                value={form.model_override}
                onChange={(e) => setField("model_override", e.target.value)}
                className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink"
              />
            </label>
            <label className="block text-sm sm:col-span-2">
              <span className="mb-1 block text-muted">{t("admin.ai.queues.serviceUser")}</span>
              <select
                data-testid="admin-ai-queue-form-service_user_id"
                value={form.service_user_id}
                onChange={(e) => setField("service_user_id", e.target.value)}
                className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink"
              >
                <option value="">{t("admin.form.selectPlaceholder")}</option>
                {(agentsQ.data ?? []).map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.full_name} ({a.login})
                  </option>
                ))}
              </select>
            </label>
          </section>

          <section className="grid gap-3 sm:grid-cols-2">
            <label className="block text-sm">
              <span className="mb-1 block text-muted">{t("admin.ai.queues.kbTags")}</span>
              <input
                data-testid="admin-ai-queue-form-kb_tags"
                value={form.kb_tags}
                onChange={(e) => setField("kb_tags", e.target.value)}
                placeholder={t("admin.ai.queues.kbTagsPlaceholder")}
                className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink"
              />
            </label>
            <label className="block text-sm">
              <span className="mb-1 block text-muted">{t("admin.ai.queues.kbCategoryIds")}</span>
              <input
                data-testid="admin-ai-queue-form-kb_category_ids"
                value={form.kb_category_ids}
                onChange={(e) => setField("kb_category_ids", e.target.value)}
                placeholder="1,2,3"
                className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink"
              />
            </label>
          </section>

          <section className="space-y-2">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">
              {t("admin.ai.queues.mcpClients")}
            </h3>
            <div className="flex flex-wrap gap-2">
              {(mcpQ.data?.items ?? []).map((c) => (
                <label
                  key={c.id}
                  className="flex items-center gap-1.5 rounded-md border border-hairline bg-surface-subtle px-2 py-1 text-xs text-ink"
                >
                  <input
                    type="checkbox"
                    data-testid={`admin-ai-queue-form-mcp-${c.id}`}
                    checked={form.mcp_client_ids.has(c.id)}
                    onChange={() => toggleMcpClient(c.id)}
                    className="rounded border-hairline"
                  />
                  {c.name}
                </label>
              ))}
              {(mcpQ.data?.items.length ?? 0) === 0 && (
                <span className="text-xs text-muted">{t("admin.ai.mcp.empty")}</span>
              )}
            </div>
          </section>

          <section className="grid gap-3 sm:grid-cols-2">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted sm:col-span-2">
              {t("admin.ai.queues.section.summary")}
            </h3>
            <label className="block text-sm">
              <span className="mb-1 block text-muted">{t("admin.ai.queues.summaryArticleThreshold")}</span>
              <input
                type="number"
                data-testid="admin-ai-queue-form-summary_article_threshold"
                value={form.summary_article_threshold}
                onChange={(e) => setField("summary_article_threshold", e.target.value)}
                className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink"
              />
            </label>
            <label className="block text-sm">
              <span className="mb-1 block text-muted">{t("admin.ai.queues.summaryCharThreshold")}</span>
              <input
                type="number"
                data-testid="admin-ai-queue-form-summary_char_threshold"
                value={form.summary_char_threshold}
                onChange={(e) => setField("summary_char_threshold", e.target.value)}
                className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink"
              />
            </label>
            <label className="block text-sm">
              <span className="mb-1 block text-muted">
                {t("admin.ai.queues.summaryIncrementalMinArticles")}
              </span>
              <input
                type="number"
                data-testid="admin-ai-queue-form-summary_incremental_min_articles"
                value={form.summary_incremental_min_articles}
                onChange={(e) => setField("summary_incremental_min_articles", e.target.value)}
                className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink"
              />
            </label>
            <label className="block text-sm">
              <span className="mb-1 block text-muted">
                {t("admin.ai.queues.summaryIncrementalMinChars")}
              </span>
              <input
                type="number"
                data-testid="admin-ai-queue-form-summary_incremental_min_chars"
                value={form.summary_incremental_min_chars}
                onChange={(e) => setField("summary_incremental_min_chars", e.target.value)}
                className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink"
              />
            </label>
          </section>

          <section className="grid gap-3 sm:grid-cols-2">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted sm:col-span-2">
              {t("admin.ai.queues.section.caps")}
            </h3>
            <label className="block text-sm">
              <span className="mb-1 block text-muted">{t("admin.ai.queues.maxClarifications")}</span>
              <input
                type="number"
                data-testid="admin-ai-queue-form-max_clarifications"
                value={form.max_clarifications}
                onChange={(e) => setField("max_clarifications", e.target.value)}
                className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink"
              />
            </label>
            <label className="block text-sm">
              <span className="mb-1 block text-muted">{t("admin.ai.queues.maxAutoReplies")}</span>
              <input
                type="number"
                data-testid="admin-ai-queue-form-max_auto_replies"
                value={form.max_auto_replies}
                onChange={(e) => setField("max_auto_replies", e.target.value)}
                className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink"
              />
            </label>
            <label className="block text-sm">
              <span className="mb-1 block text-muted">{t("admin.ai.queues.maxRepliesPerHour")}</span>
              <input
                type="number"
                data-testid="admin-ai-queue-form-max_replies_per_hour"
                value={form.max_replies_per_hour}
                onChange={(e) => setField("max_replies_per_hour", e.target.value)}
                className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink"
              />
            </label>
            <label className="block text-sm">
              <span className="mb-1 block text-muted">{t("admin.ai.queues.budgetTokensDay")}</span>
              <input
                type="number"
                data-testid="admin-ai-queue-form-budget_tokens_day"
                value={form.budget_tokens_day}
                onChange={(e) => setField("budget_tokens_day", e.target.value)}
                className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink"
              />
            </label>
          </section>

          <section className="space-y-1">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">
              {t("admin.ai.queues.escalationRules")}
            </h3>
            <p className="text-xs text-muted">{t("admin.ai.queues.escalationRulesHint")}</p>
            <textarea
              data-testid="admin-ai-queue-form-escalation_rules"
              value={form.escalation_rules}
              onChange={(e) => setField("escalation_rules", e.target.value)}
              rows={4}
              spellCheck={false}
              className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 font-mono text-xs text-ink"
            />
            {jsonErrors.escalation && (
              <p className="text-xs text-escalation" data-testid="admin-ai-queue-form-escalation_rules-error">
                {jsonErrors.escalation}
              </p>
            )}
          </section>

          <section className="space-y-2">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">
              {t("admin.ai.queues.section.disclosure")}
            </h3>
            <label className="flex items-center gap-2 text-sm text-ink">
              <input
                type="checkbox"
                data-testid="admin-ai-queue-form-ai_disclosure_enabled"
                checked={form.ai_disclosure_enabled}
                onChange={(e) => setField("ai_disclosure_enabled", e.target.checked)}
                className="rounded border-hairline"
              />
              {t("admin.ai.queues.disclosureEnabled")}
            </label>
            <textarea
              data-testid="admin-ai-queue-form-ai_disclosure_text"
              value={form.ai_disclosure_text}
              onChange={(e) => setField("ai_disclosure_text", e.target.value)}
              placeholder={t("admin.ai.queues.disclosureTextPlaceholder")}
              rows={2}
              className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink"
            />
            <label className="flex items-center gap-2 text-sm text-ink">
              <input
                type="checkbox"
                data-testid="admin-ai-queue-form-pii_masking"
                checked={form.pii_masking}
                onChange={(e) => setField("pii_masking", e.target.checked)}
                className="rounded border-hairline"
              />
              {t("admin.ai.queues.piiMasking")}
            </label>
          </section>

          <section className="space-y-2">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">
              {t("admin.ai.queues.section.identity")}
            </h3>
            <select
              data-testid="admin-ai-queue-form-identity_mode"
              value={form.identity_mode}
              onChange={(e) => setField("identity_mode", e.target.value as IdentityMode)}
              className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink"
            >
              {IDENTITY_MODES.map((m) => (
                <option key={m} value={m}>
                  {t(`admin.ai.queues.identityMode.${m}`)}
                </option>
              ))}
            </select>
            {form.identity_mode === "clarify_schema" && (
              <>
                <textarea
                  data-testid="admin-ai-queue-form-clarify_schema_json"
                  value={form.clarify_schema_json}
                  onChange={(e) => setField("clarify_schema_json", e.target.value)}
                  rows={3}
                  spellCheck={false}
                  className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 font-mono text-xs text-ink"
                />
                {jsonErrors.clarify && (
                  <p className="text-xs text-escalation" data-testid="admin-ai-queue-form-clarify_schema_json-error">
                    {jsonErrors.clarify}
                  </p>
                )}
              </>
            )}
          </section>

          {formError && (
            <p className="text-sm text-escalation" data-testid="admin-ai-queue-form-error">
              {formError}
            </p>
          )}

          <div className="flex justify-end gap-2 border-t border-hairline pt-3">
            <Button type="button" variant="ghost" onClick={() => setDialogOpen(false)}>
              {t("admin.form.cancel")}
            </Button>
            <Button
              type="button"
              variant="primary"
              data-testid="admin-ai-queue-form-submit"
              disabled={createM.isPending || updateM.isPending}
              onClick={() => void handleSubmit()}
            >
              {createM.isPending || updateM.isPending ? t("admin.form.saving") : t("admin.form.save")}
            </Button>
          </div>
        </div>
      </Dialog>
    </div>
  );
}
