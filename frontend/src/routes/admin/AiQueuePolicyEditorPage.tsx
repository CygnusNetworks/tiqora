import { useEffect, useMemo, useRef, useState, type ChangeEvent, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams, Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { api, ApiError, type QueueRef } from "@/lib/api";
import {
  aiApi,
  type AiQueuePolicyCreate,
  type AiQueuePolicyOut,
  type AiQueuePolicyUpdate,
  type Autonomy,
  type IdentityMode,
  type ReplyLanguageMode,
} from "@/lib/aiApi";
import { PickerField } from "@/components/admin/PickerField";
import { HelpPopover } from "@/components/ui/HelpPopover";
import { TagInput } from "@/components/ui/TagInput";
import { Tabs, type TabItem } from "@/components/ui/Tabs";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { cn } from "@/lib/cn";

const NONE = 0;
const MAX_PROMPT_FILE_BYTES = 2 * 1024 * 1024;
const PROMPT_WARN_CHARS = 100_000;

const POLICIES_KEY = ["admin", "ai", "queue-policies"] as const;
const QUEUES_KEY = ["admin", "ai", "reference-queues"] as const;
const PROVIDERS_KEY = ["admin", "ai", "providers"] as const;
const MCP_KEY = ["admin", "ai", "mcp-clients"] as const;
const AGENTS_KEY = ["admin", "ai", "reference-agents"] as const;
const SETTINGS_KEY = ["admin", "ai", "settings"] as const;

const AUTONOMY_VALUES: Autonomy[] = ["off", "clarify_only", "full"];
const IDENTITY_MODES: IdentityMode[] = ["ticket_customer_id", "clarify_schema", "off"];
const REPLY_LANGUAGE_MODES: ReplyLanguageMode[] = ["off", "fixed", "auto"];

type TabId = "basics" | "drafts" | "summaries" | "auto" | "safety";

type FormState = {
  queue_id: number;
  enabled_auto_reply: boolean;
  enabled_summary: boolean;
  enabled_manual_assist: boolean;
  autonomy: Autonomy;
  system_prompt: string;
  llm_provider_id: number;
  model_override: string;
  vision_provider_id: number;
  service_user_id: number;
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
  ignored_senders: string;
  ignore_senders_manual: boolean;
  reply_language_mode: ReplyLanguageMode;
  reply_language_fixed: string;
  reply_language_default: string;
  allowed_state_types: string;
};

/**
 * Defaults for a brand-new policy — chosen so a queue can go live without the
 * operator hand-tuning every cap. Never used on edit; existing policies
 * always load their real stored values via `toForm`.
 */
function emptyForm(queueId: number): FormState {
  return {
    queue_id: queueId,
    enabled_auto_reply: false,
    enabled_summary: false,
    enabled_manual_assist: false,
    autonomy: "off",
    system_prompt: "",
    llm_provider_id: NONE,
    model_override: "",
    vision_provider_id: NONE,
    service_user_id: NONE,
    kb_tags: "",
    kb_category_ids: "",
    mcp_client_ids: new Set(),
    summary_article_threshold: "10",
    summary_char_threshold: "20000",
    summary_incremental_min_articles: "2",
    summary_incremental_min_chars: "2000",
    max_clarifications: "2",
    max_auto_replies: "5",
    max_replies_per_hour: "20",
    budget_tokens_day: "500000",
    escalation_rules: "",
    ai_disclosure_enabled: false,
    ai_disclosure_text: "",
    pii_masking: true,
    identity_mode: "ticket_customer_id",
    clarify_schema_json: "",
    ignored_senders: "",
    ignore_senders_manual: false,
    reply_language_mode: "off",
    reply_language_fixed: "",
    reply_language_default: "",
    allowed_state_types: "",
  };
}

/** Newline/comma-tolerant textarea <-> stored-string conversion for
 * `ignored_senders` (one address/glob per line in the UI, comma-joined on
 * the wire — matches the backend's tolerant JSON-or-CSV parsing). */
function sendersRawToLines(raw: string | null): string {
  if (!raw || !raw.trim()) return "";
  const trimmed = raw.trim();
  try {
    const parsed: unknown = JSON.parse(trimmed);
    if (Array.isArray(parsed)) {
      return parsed
        .map((s) => String(s).trim())
        .filter(Boolean)
        .join("\n");
    }
  } catch {
    // not JSON — fall through to CSV/newline splitting
  }
  return trimmed
    .split(/[\n,]/)
    .map((s) => s.trim())
    .filter(Boolean)
    .join("\n");
}

function linesToSendersRaw(text: string): string | null {
  const items = text
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
  return items.length > 0 ? items.join(",") : null;
}

function toForm(row: AiQueuePolicyOut): FormState {
  return {
    queue_id: row.queue_id,
    enabled_auto_reply: row.enabled_auto_reply,
    enabled_summary: row.enabled_summary,
    enabled_manual_assist: row.enabled_manual_assist,
    autonomy: row.autonomy,
    system_prompt: row.system_prompt,
    llm_provider_id: row.llm_provider_id ?? NONE,
    model_override: row.model_override ?? "",
    vision_provider_id: row.vision_provider_id ?? NONE,
    service_user_id: row.service_user_id ?? NONE,
    kb_tags: row.kb_tags ?? "",
    kb_category_ids: row.kb_category_ids ?? "",
    mcp_client_ids: new Set(
      (row.mcp_client_ids ?? "")
        .split(",")
        .map((s) => s.trim())
        .filter((s) => s !== "")
        .map(Number)
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
    ignored_senders: sendersRawToLines(row.ignored_senders),
    ignore_senders_manual: row.ignore_senders_manual,
    reply_language_mode: row.reply_language_mode,
    reply_language_fixed: row.reply_language_fixed ?? "",
    reply_language_default: row.reply_language_default ?? "",
    allowed_state_types: row.allowed_state_types ?? "",
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

/** Field label + inline {@link HelpPopover} trigger — used on every editor field. */
function FieldLabel({
  text,
  help,
  defaultHint,
  testId,
}: {
  text: string;
  help: ReactNode;
  defaultHint?: string;
  testId: string;
}) {
  return (
    <span className="mb-1 flex items-center gap-1.5 text-muted">
      {text}
      <HelpPopover title={text} defaultHint={defaultHint} testId={testId}>
        {help}
      </HelpPopover>
    </span>
  );
}

/** Tab label with a green "feature enabled" dot, matching the artefact mock. */
function TabLabel({ label, active, testId }: { label: string; active: boolean; testId?: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      {label}
      {active && <span className="h-1.5 w-1.5 rounded-full bg-green" data-testid={testId} />}
    </span>
  );
}

const inputClass =
  "w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink disabled:cursor-not-allowed disabled:opacity-50";

function AiQueuePolicyEditor({ policyId }: { policyId?: number }) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language?.startsWith("de") ? "de-DE" : "en-US";
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const isEdit = policyId != null;

  const [tab, setTab] = useState<TabId>("basics");
  const [form, setForm] = useState<FormState | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [jsonErrors, setJsonErrors] = useState<{ escalation?: string; clarify?: string }>({});
  const [promptFileError, setPromptFileError] = useState<string | null>(null);
  const autoProviderApplied = useRef(false);
  const promptFileInputRef = useRef<HTMLInputElement>(null);

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
  const settingsQ = useQuery({
    queryKey: SETTINGS_KEY,
    queryFn: ({ signal }) => aiApi.getSettings(signal),
  });
  const kbTagsQ = useQuery({
    queryKey: ["kb", "tags"],
    queryFn: ({ signal }) => api.listKbTags(signal),
  });

  const editingRow = useMemo(
    () => (isEdit ? (policiesQ.data?.items ?? []).find((p) => p.id === policyId) ?? null : null),
    [isEdit, policiesQ.data, policyId],
  );

  const availableQueues: QueueRef[] = useMemo(() => {
    const used = new Set((policiesQ.data?.items ?? []).map((p) => p.queue_id));
    return (queuesQ.data ?? []).filter((q) => !used.has(q.id));
  }, [queuesQ.data, policiesQ.data]);

  // Create: seed the form once queues are known (first available queue).
  useEffect(() => {
    if (isEdit || form) return;
    if (!queuesQ.data) return;
    setForm(emptyForm(availableQueues[0]?.id ?? NONE));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isEdit, queuesQ.data]);

  // Edit: load the real stored values once the policy row is resolved.
  useEffect(() => {
    if (!isEdit || !editingRow) return;
    setForm(toForm(editingRow));
  }, [isEdit, editingRow]);

  // Create only, once: auto-pick the sole configured provider (never override
  // a value the operator already touched or that came from a stored policy).
  useEffect(() => {
    if (isEdit || autoProviderApplied.current || !form || !providersQ.data) return;
    if (form.llm_provider_id !== NONE) return;
    if (providersQ.data.items.length === 1) {
      autoProviderApplied.current = true;
      const providerId = providersQ.data.items[0].id;
      setForm((f) => (f ? { ...f, llm_provider_id: providerId } : f));
    }
  }, [isEdit, form, providersQ.data]);

  const setField = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((f) => (f ? { ...f, [key]: value } : f));

  const toggleMcpClient = (id: number) => {
    setForm((f) => {
      if (!f) return f;
      const next = new Set(f.mcp_client_ids);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return { ...f, mcp_client_ids: next };
    });
  };

  const applyPromptFileContent = (text: string) => {
    // A null byte (or a very high ratio of other non-printable control
    // characters) means the "text" file isn't actually text --
    // `FileReader.readAsText` would otherwise silently hand back mojibake
    // instead of failing.
    let controlChars = 0;
    for (let i = 0; i < text.length; i++) {
      const code = text.charCodeAt(i);
      if (code === 0 || (code < 32 && code !== 9 && code !== 10 && code !== 13)) controlChars++;
    }
    if (controlChars / Math.max(text.length, 1) > 0.01) {
      setPromptFileError(t("admin.ai.queues.promptFileBinary"));
      return;
    }
    setPromptFileError(null);
    setField("system_prompt", text);
  };

  const readFileAsText = (file: File): Promise<string> =>
    new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(typeof reader.result === "string" ? reader.result : "");
      reader.onerror = () => reject(reader.error ?? new Error("file read failed"));
      reader.readAsText(file);
    });

  const handlePromptFileSelected = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setPromptFileError(null);

    if (file.size > MAX_PROMPT_FILE_BYTES) {
      setPromptFileError(t("admin.ai.queues.promptFileTooLarge"));
      return;
    }

    let text: string;
    try {
      text = await readFileAsText(file);
    } catch {
      setPromptFileError(t("admin.ai.queues.promptFileBinary"));
      return;
    }
    if (form && form.system_prompt.trim()) {
      const ok = await confirm({
        title: t("admin.ai.queues.promptReplaceConfirmTitle"),
        message: t("admin.ai.queues.promptReplaceConfirmMessage"),
      });
      if (!ok) return;
    }
    applyPromptFileContent(text);
  };

  const invalidate = () => qc.invalidateQueries({ queryKey: POLICIES_KEY });

  const createM = useMutation({
    mutationFn: (body: AiQueuePolicyCreate) => aiApi.createQueuePolicy(body),
    onSuccess: async () => {
      await invalidate();
      await navigate({ to: "/admin/ai/queues" });
    },
  });
  const updateM = useMutation({
    mutationFn: ({ id, body }: { id: number; body: AiQueuePolicyUpdate }) =>
      aiApi.updateQueuePolicy(id, body),
    onSuccess: async () => {
      await invalidate();
      await navigate({ to: "/admin/ai/queues" });
    },
  });

  const buildBody = (f: FormState): AiQueuePolicyCreate | AiQueuePolicyUpdate => ({
    enabled_auto_reply: f.enabled_auto_reply,
    enabled_summary: f.enabled_summary,
    enabled_manual_assist: f.enabled_manual_assist,
    autonomy: f.autonomy,
    system_prompt: f.system_prompt,
    llm_provider_id: f.llm_provider_id !== NONE ? f.llm_provider_id : null,
    model_override: f.model_override.trim() || null,
    vision_provider_id: f.vision_provider_id !== NONE ? f.vision_provider_id : null,
    service_user_id: f.service_user_id !== NONE ? f.service_user_id : null,
    kb_tags: f.kb_tags.trim() || null,
    kb_category_ids: f.kb_category_ids.trim() || null,
    mcp_client_ids: f.mcp_client_ids.size > 0 ? Array.from(f.mcp_client_ids).join(",") : null,
    summary_article_threshold: numOrNull(f.summary_article_threshold),
    summary_char_threshold: numOrNull(f.summary_char_threshold),
    summary_incremental_min_articles: numOrNull(f.summary_incremental_min_articles),
    summary_incremental_min_chars: numOrNull(f.summary_incremental_min_chars),
    max_clarifications: numOrNull(f.max_clarifications) ?? 2,
    max_auto_replies: numOrNull(f.max_auto_replies) ?? 5,
    max_replies_per_hour: numOrNull(f.max_replies_per_hour),
    budget_tokens_day: numOrNull(f.budget_tokens_day),
    escalation_rules: f.escalation_rules.trim() || null,
    ai_disclosure_enabled: f.ai_disclosure_enabled,
    ai_disclosure_text: f.ai_disclosure_text.trim() || null,
    pii_masking: f.pii_masking,
    identity_mode: f.identity_mode,
    clarify_schema_json: f.clarify_schema_json.trim() || null,
    ignored_senders: linesToSendersRaw(f.ignored_senders),
    ignore_senders_manual: f.ignore_senders_manual,
    reply_language_mode: f.reply_language_mode,
    reply_language_fixed: f.reply_language_fixed.trim() || null,
    reply_language_default: f.reply_language_default.trim() || null,
    allowed_state_types: f.allowed_state_types.trim() || null,
  });

  const handleSave = async () => {
    if (!form) return;
    setFormError(null);
    const escalationErr = validateJson("escalation_rules", form.escalation_rules, t);
    const clarifyErr = validateJson("clarify_schema_json", form.clarify_schema_json, t);
    setJsonErrors({ escalation: escalationErr ?? undefined, clarify: clarifyErr ?? undefined });
    if (escalationErr || clarifyErr) return;

    try {
      if (isEdit) {
        await updateM.mutateAsync({ id: policyId, body: buildBody(form) });
      } else {
        if (form.queue_id === NONE) {
          setFormError(t("admin.ai.queues.queueRequired"));
          return;
        }
        await createM.mutateAsync({ queue_id: form.queue_id, ...buildBody(form) });
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setFormError(t("admin.ai.queues.gateError"));
      } else {
        setFormError(err instanceof ApiError ? err.message : t("admin.form.genericError"));
      }
    }
  };

  const isLoading = isEdit ? policiesQ.isLoading || (policiesQ.isSuccess && !editingRow) : !form;

  if (isEdit && policiesQ.isSuccess && !editingRow) {
    return (
      <div className="p-4 text-sm text-danger" data-testid="admin-ai-queue-editor-page">
        {t("admin.ai.queues.editor.loadError")}
      </div>
    );
  }

  if (isLoading || !form) {
    return (
      <div className="flex items-center gap-2 p-4" data-testid="admin-ai-queue-editor-page">
        <Spinner />
      </div>
    );
  }

  const gateOpen = settingsQ.data?.operation_mode === "tiqora_primary";
  const queueName =
    (queuesQ.data ?? []).find((q) => q.id === form.queue_id)?.name ?? `#${form.queue_id}`;

  const tabs: TabItem[] = [
    { id: "basics", label: t("admin.ai.queues.tab.basics") },
    {
      id: "drafts",
      label: (
        <TabLabel
          label={t("admin.ai.queues.tab.drafts")}
          active={form.enabled_manual_assist}
          testId="admin-ai-queue-tab-dot-drafts"
        />
      ),
    },
    {
      id: "summaries",
      label: (
        <TabLabel
          label={t("admin.ai.queues.tab.summaries")}
          active={form.enabled_summary}
          testId="admin-ai-queue-tab-dot-summaries"
        />
      ),
    },
    {
      id: "auto",
      label: (
        <TabLabel
          label={t("admin.ai.queues.tab.auto")}
          active={form.enabled_auto_reply}
          testId="admin-ai-queue-tab-dot-auto"
        />
      ),
    },
    { id: "safety", label: t("admin.ai.queues.tab.safety") },
  ];

  return (
    <div className="mx-auto w-full max-w-4xl space-y-4 p-4" data-testid="admin-ai-queue-editor-page">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="font-display text-xl font-semibold text-ink">
            {isEdit ? queueName : t("admin.ai.queues.editor.newTitle")}
          </h1>
          <p className="text-xs text-muted">{t("admin.ai.queues.description")}</p>
        </div>
        <div className="flex items-center gap-2">
          <Link
            to="/admin/ai/queues"
            className="text-sm text-muted hover:text-ink hover:underline"
            data-testid="admin-ai-queue-editor-cancel"
          >
            {t("admin.form.cancel")}
          </Link>
          <Button
            type="button"
            variant="primary"
            data-testid="admin-ai-queue-editor-save"
            disabled={createM.isPending || updateM.isPending}
            onClick={() => void handleSave()}
          >
            {createM.isPending || updateM.isPending ? t("admin.form.saving") : t("admin.form.save")}
          </Button>
        </div>
      </div>

      <Tabs items={tabs} value={tab} onChange={(id) => setTab(id as TabId)} />

      {formError && (
        <p className="text-sm text-escalation" data-testid="admin-ai-queue-editor-error">
          {formError}
        </p>
      )}

      <div className="rounded-lg border border-hairline bg-surface p-4">
        {tab === "basics" && (
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block text-sm sm:col-span-2">
              <FieldLabel
                text={t("admin.ai.queues.queue")}
                help={t("admin.help.aiQueue.queue")}
                testId="admin-ai-queue-help-queue"
              />
              {isEdit ? (
                <>
                  <p className="rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-muted">
                    {queueName}
                  </p>
                  <p className="mt-1 text-xs text-muted">{t("admin.ai.queues.editor.queueLocked")}</p>
                </>
              ) : (
                <PickerField
                  testId="admin-ai-queue-form-queue_id"
                  value={form.queue_id}
                  items={availableQueues.map((q) => ({ value: q.id, label: q.name }))}
                  placeholder={t("admin.form.selectPlaceholder")}
                  onSelect={(v) => setField("queue_id", v)}
                />
              )}
            </label>

            <div className="block text-sm sm:col-span-2">
              <div className="mb-1 flex items-center justify-between gap-2">
                <FieldLabel
                  text={t("admin.ai.queues.section.prompt")}
                  help={t("admin.help.aiQueue.systemPrompt")}
                  testId="admin-ai-queue-help-system_prompt"
                />
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  data-testid="admin-ai-queue-form-prompt-load-file"
                  onClick={() => promptFileInputRef.current?.click()}
                >
                  {t("admin.ai.queues.promptLoadFile")}
                </Button>
                <input
                  ref={promptFileInputRef}
                  type="file"
                  accept=".txt,.md,text/plain,text/markdown"
                  data-testid="admin-ai-queue-form-prompt-file-input"
                  className="hidden"
                  onChange={(e) => void handlePromptFileSelected(e)}
                />
              </div>
              <textarea
                data-testid="admin-ai-queue-form-system_prompt"
                value={form.system_prompt}
                onChange={(e) => setField("system_prompt", e.target.value)}
                rows={14}
                className={cn(inputClass, "font-mono text-xs")}
              />
              <div className="mt-1 flex flex-wrap items-center justify-between gap-2 text-xs">
                <span className="text-muted" data-testid="admin-ai-queue-form-prompt-char-count">
                  {t("admin.ai.queues.promptCharCount", {
                    formatted: new Intl.NumberFormat(locale).format(form.system_prompt.length),
                  })}
                </span>
                {form.system_prompt.length >= PROMPT_WARN_CHARS && (
                  <span className="text-amber" data-testid="admin-ai-queue-form-prompt-char-warning">
                    {t("admin.ai.queues.promptCharCountWarning")}
                  </span>
                )}
              </div>
              {promptFileError && (
                <p className="mt-1 text-xs text-escalation" data-testid="admin-ai-queue-form-prompt-file-error">
                  {promptFileError}
                </p>
              )}
            </div>

            <label className="block text-sm">
              <FieldLabel
                text={t("admin.ai.providers.title")}
                help={t("admin.help.aiQueue.llmProvider")}
                testId="admin-ai-queue-help-llm_provider_id"
              />
              <PickerField
                testId="admin-ai-queue-form-llm_provider_id"
                value={form.llm_provider_id}
                items={[
                  { value: NONE, label: t("admin.form.selectPlaceholder") },
                  ...(providersQ.data?.items ?? []).map((p) => ({ value: p.id, label: p.name })),
                ]}
                placeholder={t("admin.form.selectPlaceholder")}
                loading={providersQ.isLoading}
                onSelect={(v) => setField("llm_provider_id", v)}
              />
            </label>
            <label className="block text-sm">
              <FieldLabel
                text={t("admin.ai.queues.modelOverride")}
                help={t("admin.help.aiQueue.modelOverride")}
                testId="admin-ai-queue-help-model_override"
              />
              <input
                data-testid="admin-ai-queue-form-model_override"
                value={form.model_override}
                onChange={(e) => setField("model_override", e.target.value)}
                className={inputClass}
              />
            </label>
            <label className="block text-sm sm:col-span-2">
              <FieldLabel
                text={t("admin.ai.queues.visionProvider")}
                help={t("admin.help.aiQueue.visionProvider")}
                defaultHint={t("admin.ai.queues.visionProviderNone")}
                testId="admin-ai-queue-help-vision_provider_id"
              />
              <PickerField
                testId="admin-ai-queue-form-vision_provider_id"
                value={form.vision_provider_id}
                items={[
                  { value: NONE, label: t("admin.ai.queues.visionProviderNone") },
                  ...(providersQ.data?.items ?? [])
                    .filter((p) => p.supports_vision)
                    .map((p) => ({ value: p.id, label: p.name })),
                ]}
                placeholder={t("admin.form.selectPlaceholder")}
                loading={providersQ.isLoading}
                onSelect={(v) => setField("vision_provider_id", v)}
              />
            </label>
          </div>
        )}

        {tab === "drafts" && (
          <div className="space-y-4">
            <label className="flex items-center gap-2 text-sm text-ink">
              <input
                type="checkbox"
                data-testid="admin-ai-queue-form-enabled_manual_assist"
                checked={form.enabled_manual_assist}
                onChange={(e) => setField("enabled_manual_assist", e.target.checked)}
                className="rounded border-hairline"
              />
              {t("admin.ai.feature.manual_assist")}
              <HelpPopover
                title={t("admin.ai.feature.manual_assist")}
                testId="admin-ai-queue-help-enabled_manual_assist"
              >
                {t("admin.help.aiQueue.enabledManualAssist")}
              </HelpPopover>
            </label>

            <fieldset
              disabled={!form.enabled_manual_assist}
              className={cn(
                "grid gap-4 sm:grid-cols-2",
                !form.enabled_manual_assist && "opacity-50",
              )}
            >
              <label className="block text-sm">
                <FieldLabel
                  text={t("admin.ai.queues.kbTags")}
                  help={t("admin.help.aiQueue.kbTags")}
                  testId="admin-ai-queue-help-kb_tags"
                />
                <TagInput
                  testId="admin-ai-queue-form-kb_tags"
                  value={form.kb_tags.split(",").map((s) => s.trim()).filter(Boolean)}
                  onChange={(tags) => setField("kb_tags", tags.join(","))}
                  suggestions={(kbTagsQ.data ?? []).map((tg) => ({
                    name: tg.name,
                    count: tg.article_count,
                  }))}
                  placeholder={t("admin.ai.queues.kbTagsPlaceholder")}
                />
              </label>
              <label className="block text-sm">
                <FieldLabel
                  text={t("admin.ai.queues.kbCategoryIds")}
                  help={t("admin.help.aiQueue.kbCategoryIds")}
                  testId="admin-ai-queue-help-kb_category_ids"
                />
                <input
                  data-testid="admin-ai-queue-form-kb_category_ids"
                  value={form.kb_category_ids}
                  onChange={(e) => setField("kb_category_ids", e.target.value)}
                  placeholder="1,2,3"
                  className={inputClass}
                />
              </label>
              <div className="sm:col-span-2">
                <div className="mb-1 flex items-center gap-1.5">
                  <FieldLabel
                    text={t("admin.ai.queues.mcpClients")}
                    help={t("admin.help.aiQueue.mcpClients")}
                    testId="admin-ai-queue-help-mcp_clients"
                  />
                  <span
                    className="text-xs text-muted"
                    data-testid="admin-ai-queue-form-mcp-selected-count"
                  >
                    {t("admin.ai.queues.mcpClientsSelected", { count: form.mcp_client_ids.size })}
                  </span>
                </div>
                <div className="flex flex-wrap gap-2">
                  {(mcpQ.data?.items ?? []).map((c) => (
                    <label
                      key={c.id}
                      className="flex items-start gap-1.5 rounded-md border border-hairline bg-surface-subtle px-2 py-1.5 text-xs text-ink"
                    >
                      <input
                        type="checkbox"
                        data-testid={`admin-ai-queue-form-mcp-${c.id}`}
                        checked={form.mcp_client_ids.has(c.id)}
                        onChange={() => toggleMcpClient(c.id)}
                        className="mt-0.5 rounded border-hairline"
                      />
                      <span>
                        <span className="block">{c.name}</span>
                        <span className="block text-[11px] text-muted">{c.url}</span>
                      </span>
                    </label>
                  ))}
                  {(mcpQ.data?.items.length ?? 0) === 0 && (
                    <span className="text-xs text-muted">{t("admin.ai.mcp.empty")}</span>
                  )}
                </div>
              </div>
            </fieldset>
          </div>
        )}

        {tab === "summaries" && (
          <div className="space-y-4">
            <label className="flex items-center gap-2 text-sm text-ink">
              <input
                type="checkbox"
                data-testid="admin-ai-queue-form-enabled_summary"
                checked={form.enabled_summary}
                onChange={(e) => setField("enabled_summary", e.target.checked)}
                className="rounded border-hairline"
              />
              {t("admin.ai.feature.summary")}
              <HelpPopover title={t("admin.ai.feature.summary")} testId="admin-ai-queue-help-enabled_summary">
                {t("admin.help.aiQueue.enabledSummary")}
              </HelpPopover>
            </label>

            <fieldset
              disabled={!form.enabled_summary}
              className={cn("grid gap-4 sm:grid-cols-2", !form.enabled_summary && "opacity-50")}
            >
              <label className="block text-sm">
                <FieldLabel
                  text={t("admin.ai.queues.summaryArticleThreshold")}
                  help={t("admin.help.aiQueue.summaryArticleThreshold")}
                  defaultHint={t("admin.ai.queues.emptyUnlimited")}
                  testId="admin-ai-queue-help-summary_article_threshold"
                />
                <input
                  type="number"
                  data-testid="admin-ai-queue-form-summary_article_threshold"
                  value={form.summary_article_threshold}
                  onChange={(e) => setField("summary_article_threshold", e.target.value)}
                  placeholder={t("admin.ai.queues.emptyUnlimited")}
                  className={inputClass}
                />
              </label>
              <label className="block text-sm">
                <FieldLabel
                  text={t("admin.ai.queues.summaryCharThreshold")}
                  help={t("admin.help.aiQueue.summaryCharThreshold")}
                  defaultHint={t("admin.ai.queues.emptyUnlimited")}
                  testId="admin-ai-queue-help-summary_char_threshold"
                />
                <input
                  type="number"
                  data-testid="admin-ai-queue-form-summary_char_threshold"
                  value={form.summary_char_threshold}
                  onChange={(e) => setField("summary_char_threshold", e.target.value)}
                  placeholder={t("admin.ai.queues.emptyUnlimited")}
                  className={inputClass}
                />
              </label>
              <label className="block text-sm">
                <FieldLabel
                  text={t("admin.ai.queues.summaryIncrementalMinArticles")}
                  help={t("admin.help.aiQueue.summaryIncrementalMinArticles")}
                  defaultHint={t("admin.ai.queues.emptyUnlimited")}
                  testId="admin-ai-queue-help-summary_incremental_min_articles"
                />
                <input
                  type="number"
                  data-testid="admin-ai-queue-form-summary_incremental_min_articles"
                  value={form.summary_incremental_min_articles}
                  onChange={(e) => setField("summary_incremental_min_articles", e.target.value)}
                  placeholder={t("admin.ai.queues.emptyUnlimited")}
                  className={inputClass}
                />
              </label>
              <label className="block text-sm">
                <FieldLabel
                  text={t("admin.ai.queues.summaryIncrementalMinChars")}
                  help={t("admin.help.aiQueue.summaryIncrementalMinChars")}
                  defaultHint={t("admin.ai.queues.emptyUnlimited")}
                  testId="admin-ai-queue-help-summary_incremental_min_chars"
                />
                <input
                  type="number"
                  data-testid="admin-ai-queue-form-summary_incremental_min_chars"
                  value={form.summary_incremental_min_chars}
                  onChange={(e) => setField("summary_incremental_min_chars", e.target.value)}
                  placeholder={t("admin.ai.queues.emptyUnlimited")}
                  className={inputClass}
                />
              </label>
            </fieldset>
          </div>
        )}

        {tab === "auto" && (
          <div className="space-y-4">
            <div className="space-y-2 border-b border-hairline pb-4">
              <FieldLabel
                text={t("admin.ai.queues.ignoredSenders")}
                help={t("admin.help.aiQueue.ignoredSenders")}
                testId="admin-ai-queue-help-ignored_senders"
              />
              <textarea
                data-testid="admin-ai-queue-form-ignored_senders"
                value={form.ignored_senders}
                onChange={(e) => setField("ignored_senders", e.target.value)}
                placeholder={t("admin.ai.queues.ignoredSendersPlaceholder")}
                rows={3}
                spellCheck={false}
                className={cn(inputClass, "font-mono text-xs")}
              />
              <label className="flex items-center gap-2 text-sm text-ink">
                <input
                  type="checkbox"
                  data-testid="admin-ai-queue-form-ignore_senders_manual"
                  checked={form.ignore_senders_manual}
                  onChange={(e) => setField("ignore_senders_manual", e.target.checked)}
                  className="rounded border-hairline"
                />
                {t("admin.ai.queues.ignoreSendersManual")}
                <HelpPopover
                  title={t("admin.ai.queues.ignoreSendersManual")}
                  testId="admin-ai-queue-help-ignore_senders_manual"
                >
                  {t("admin.help.aiQueue.ignoreSendersManual")}
                </HelpPopover>
              </label>
            </div>

            <div className="grid gap-4 border-b border-hairline pb-4 sm:grid-cols-2">
              <label className="block text-sm">
                <FieldLabel
                  text={t("admin.ai.queues.replyLanguageMode.label")}
                  help={t("admin.help.aiQueue.replyLanguageMode")}
                  defaultHint={t("admin.ai.queues.replyLanguageMode.off")}
                  testId="admin-ai-queue-help-reply_language_mode"
                />
                <PickerField
                  testId="admin-ai-queue-form-reply_language_mode"
                  value={form.reply_language_mode}
                  items={REPLY_LANGUAGE_MODES.map((m) => ({
                    value: m,
                    label: t(`admin.ai.queues.replyLanguageMode.${m}`),
                  }))}
                  placeholder={t("admin.form.selectPlaceholder")}
                  onSelect={(v) => setField("reply_language_mode", v)}
                />
              </label>
              {form.reply_language_mode === "fixed" && (
                <label className="block text-sm">
                  <FieldLabel
                    text={t("admin.ai.queues.replyLanguageFixed")}
                    help={t("admin.help.aiQueue.replyLanguageFixed")}
                    testId="admin-ai-queue-help-reply_language_fixed"
                  />
                  <input
                    data-testid="admin-ai-queue-form-reply_language_fixed"
                    value={form.reply_language_fixed}
                    onChange={(e) => setField("reply_language_fixed", e.target.value)}
                    placeholder="de"
                    className={inputClass}
                  />
                </label>
              )}
              {form.reply_language_mode === "auto" && (
                <label className="block text-sm">
                  <FieldLabel
                    text={t("admin.ai.queues.replyLanguageDefault")}
                    help={t("admin.help.aiQueue.replyLanguageDefault")}
                    testId="admin-ai-queue-help-reply_language_default"
                  />
                  <input
                    data-testid="admin-ai-queue-form-reply_language_default"
                    value={form.reply_language_default}
                    onChange={(e) => setField("reply_language_default", e.target.value)}
                    placeholder="de"
                    className={inputClass}
                  />
                </label>
              )}
            </div>

            {!gateOpen && (
              <p
                className="rounded-md border border-escalation/40 bg-escalation/10 p-2 text-xs text-escalation"
                data-testid="admin-ai-queue-auto-gate-warning"
              >
                {t("admin.ai.queues.autoGateWarning")}
              </p>
            )}
            <label className="flex items-center gap-2 text-sm text-ink">
              <input
                type="checkbox"
                data-testid="admin-ai-queue-form-enabled_auto_reply"
                checked={form.enabled_auto_reply}
                disabled={!gateOpen && !form.enabled_auto_reply}
                onChange={(e) => setField("enabled_auto_reply", e.target.checked)}
                className="rounded border-hairline disabled:cursor-not-allowed disabled:opacity-50"
              />
              {t("admin.ai.feature.auto_reply")}
              <HelpPopover title={t("admin.ai.feature.auto_reply")} testId="admin-ai-queue-help-enabled_auto_reply">
                {t("admin.help.aiQueue.enabledAutoReply")}
              </HelpPopover>
            </label>

            <fieldset
              disabled={!form.enabled_auto_reply}
              className={cn("space-y-4", !form.enabled_auto_reply && "opacity-50")}
            >
              <div className="grid gap-4 sm:grid-cols-2">
                <label className="block text-sm">
                  <FieldLabel
                    text={t("admin.ai.queues.autonomy.label")}
                    help={t("admin.help.aiQueue.autonomy")}
                    defaultHint={t("admin.ai.queues.autonomy.off")}
                    testId="admin-ai-queue-help-autonomy"
                  />
                  <PickerField
                    testId="admin-ai-queue-form-autonomy"
                    value={form.autonomy}
                    items={AUTONOMY_VALUES.map((a) => ({
                      value: a,
                      label: t(`admin.ai.queues.autonomy.${a}`),
                    }))}
                    placeholder={t("admin.form.selectPlaceholder")}
                    onSelect={(v) => setField("autonomy", v)}
                  />
                </label>
                <label className="block text-sm">
                  <FieldLabel
                    text={t("admin.ai.queues.serviceUser")}
                    help={t("admin.help.aiQueue.serviceUser")}
                    testId="admin-ai-queue-help-service_user_id"
                  />
                  <PickerField
                    testId="admin-ai-queue-form-service_user_id"
                    value={form.service_user_id}
                    items={[
                      { value: NONE, label: t("admin.form.selectPlaceholder") },
                      ...(agentsQ.data ?? []).map((a) => ({
                        value: a.id,
                        label: a.full_name,
                        hint: a.login,
                      })),
                    ]}
                    placeholder={t("admin.form.selectPlaceholder")}
                    loading={agentsQ.isLoading}
                    onSelect={(v) => setField("service_user_id", v)}
                  />
                </label>
                <label className="block text-sm">
                  <FieldLabel
                    text={t("admin.ai.queues.maxClarifications")}
                    help={t("admin.help.aiQueue.maxClarifications")}
                    defaultHint="2"
                    testId="admin-ai-queue-help-max_clarifications"
                  />
                  <input
                    type="number"
                    data-testid="admin-ai-queue-form-max_clarifications"
                    value={form.max_clarifications}
                    onChange={(e) => setField("max_clarifications", e.target.value)}
                    className={inputClass}
                  />
                </label>
                <label className="block text-sm">
                  <FieldLabel
                    text={t("admin.ai.queues.maxAutoReplies")}
                    help={t("admin.help.aiQueue.maxAutoReplies")}
                    defaultHint="5"
                    testId="admin-ai-queue-help-max_auto_replies"
                  />
                  <input
                    type="number"
                    data-testid="admin-ai-queue-form-max_auto_replies"
                    value={form.max_auto_replies}
                    onChange={(e) => setField("max_auto_replies", e.target.value)}
                    className={inputClass}
                  />
                </label>
                <label className="block text-sm">
                  <FieldLabel
                    text={t("admin.ai.queues.maxRepliesPerHour")}
                    help={t("admin.help.aiQueue.maxRepliesPerHour")}
                    defaultHint={t("admin.ai.queues.emptyUnlimited")}
                    testId="admin-ai-queue-help-max_replies_per_hour"
                  />
                  <input
                    type="number"
                    data-testid="admin-ai-queue-form-max_replies_per_hour"
                    value={form.max_replies_per_hour}
                    onChange={(e) => setField("max_replies_per_hour", e.target.value)}
                    placeholder={t("admin.ai.queues.emptyUnlimited")}
                    className={inputClass}
                  />
                </label>
                <label className="block text-sm">
                  <FieldLabel
                    text={t("admin.ai.queues.budgetTokensDay")}
                    help={t("admin.help.aiQueue.budgetTokensDay")}
                    defaultHint={t("admin.ai.queues.emptyUnlimited")}
                    testId="admin-ai-queue-help-budget_tokens_day"
                  />
                  <input
                    type="number"
                    data-testid="admin-ai-queue-form-budget_tokens_day"
                    value={form.budget_tokens_day}
                    onChange={(e) => setField("budget_tokens_day", e.target.value)}
                    placeholder={t("admin.ai.queues.emptyUnlimited")}
                    className={inputClass}
                  />
                </label>
              </div>

              <div className="space-y-2 border-t border-hairline pt-3">
                <label className="flex items-center gap-2 text-sm text-ink">
                  <input
                    type="checkbox"
                    data-testid="admin-ai-queue-form-ai_disclosure_enabled"
                    checked={form.ai_disclosure_enabled}
                    onChange={(e) => setField("ai_disclosure_enabled", e.target.checked)}
                    className="rounded border-hairline"
                  />
                  {t("admin.ai.queues.disclosureEnabled")}
                  <HelpPopover
                    title={t("admin.ai.queues.disclosureEnabled")}
                    testId="admin-ai-queue-help-ai_disclosure_enabled"
                  >
                    {t("admin.help.aiQueue.disclosureEnabled")}
                  </HelpPopover>
                </label>
                <label className="block text-sm">
                  <FieldLabel
                    text={t("admin.ai.queues.disclosureTextPlaceholder")}
                    help={t("admin.help.aiQueue.disclosureText")}
                    testId="admin-ai-queue-help-ai_disclosure_text"
                  />
                  <textarea
                    data-testid="admin-ai-queue-form-ai_disclosure_text"
                    value={form.ai_disclosure_text}
                    onChange={(e) => setField("ai_disclosure_text", e.target.value)}
                    placeholder={t("admin.ai.queues.disclosureTextPlaceholder")}
                    rows={2}
                    className={inputClass}
                  />
                </label>
              </div>

              <div className="space-y-1 border-t border-hairline pt-3">
                <FieldLabel
                  text={t("admin.ai.queues.escalationRules")}
                  help={t("admin.help.aiQueue.escalationRules")}
                  testId="admin-ai-queue-help-escalation_rules"
                />
                <textarea
                  data-testid="admin-ai-queue-form-escalation_rules"
                  value={form.escalation_rules}
                  onChange={(e) => setField("escalation_rules", e.target.value)}
                  placeholder={t("admin.ai.queues.escalationRulesPlaceholder")}
                  rows={4}
                  spellCheck={false}
                  className={cn(inputClass, "font-mono text-xs")}
                />
                {jsonErrors.escalation && (
                  <p
                    className="text-xs text-escalation"
                    data-testid="admin-ai-queue-form-escalation_rules-error"
                  >
                    {jsonErrors.escalation}
                  </p>
                )}
              </div>
            </fieldset>
          </div>
        )}

        {tab === "safety" && (
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="flex items-center gap-2 text-sm text-ink sm:col-span-2">
              <input
                type="checkbox"
                data-testid="admin-ai-queue-form-pii_masking"
                checked={form.pii_masking}
                onChange={(e) => setField("pii_masking", e.target.checked)}
                className="rounded border-hairline"
              />
              {t("admin.ai.queues.piiMasking")}
              <HelpPopover title={t("admin.ai.queues.piiMasking")} testId="admin-ai-queue-help-pii_masking">
                {t("admin.help.aiQueue.piiMasking")}
              </HelpPopover>
            </label>
            <label className="block text-sm sm:col-span-2">
              <FieldLabel
                text={t("admin.ai.queues.section.identity")}
                help={t("admin.help.aiQueue.identityMode")}
                defaultHint={t("admin.ai.queues.identityMode.ticket_customer_id")}
                testId="admin-ai-queue-help-identity_mode"
              />
              <PickerField
                testId="admin-ai-queue-form-identity_mode"
                value={form.identity_mode}
                items={IDENTITY_MODES.map((m) => ({
                  value: m,
                  label: t(`admin.ai.queues.identityMode.${m}`),
                }))}
                placeholder={t("admin.form.selectPlaceholder")}
                onSelect={(v) => setField("identity_mode", v)}
              />
            </label>
            <label className="block text-sm sm:col-span-2">
              <FieldLabel
                text={t("admin.ai.queues.allowedStateTypes")}
                help={t("admin.help.aiQueue.allowedStateTypes")}
                defaultHint="open"
                testId="admin-ai-queue-help-allowed_state_types"
              />
              <input
                data-testid="admin-ai-queue-form-allowed_state_types"
                value={form.allowed_state_types}
                onChange={(e) => setField("allowed_state_types", e.target.value)}
                placeholder="open"
                className={inputClass}
              />
            </label>
            {form.identity_mode === "clarify_schema" && (
              <label className="block text-sm sm:col-span-2">
                <FieldLabel
                  text={t("admin.ai.queues.section.identity")}
                  help={t("admin.help.aiQueue.clarifySchemaJson")}
                  testId="admin-ai-queue-help-clarify_schema_json"
                />
                <textarea
                  data-testid="admin-ai-queue-form-clarify_schema_json"
                  value={form.clarify_schema_json}
                  onChange={(e) => setField("clarify_schema_json", e.target.value)}
                  rows={3}
                  spellCheck={false}
                  className={cn(inputClass, "font-mono text-xs")}
                />
                {jsonErrors.clarify && (
                  <p
                    className="text-xs text-escalation"
                    data-testid="admin-ai-queue-form-clarify_schema_json-error"
                  >
                    {jsonErrors.clarify}
                  </p>
                )}
              </label>
            )}
          </div>
        )}
      </div>

      {confirmDialog}
    </div>
  );
}

export function AiQueuePolicyNewPage() {
  return <AiQueuePolicyEditor />;
}

export function AiQueuePolicyEditPage() {
  const { policyId } = useParams({ from: "/admin/ai/queues/$policyId" });
  return <AiQueuePolicyEditor policyId={Number(policyId)} />;
}
