import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  aiApi,
  type LlmModelOut,
  type LlmProfileIn,
  type LlmProfileOut,
} from "@/lib/aiApi";
import { api } from "@/lib/api";
import {
  AI_TASKS,
  AI_TASKS_FALLING_BACK_TO_AGENT,
  aiTaskNameKey,
  isAiTask,
  resolveTaskProfile,
  taskProfileMap,
  type AiTask,
} from "@/lib/aiTasks";
import {
  CrudDrawer,
  type FieldDef,
  type FieldValues,
} from "@/components/admin/CrudDrawer";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { SelectField } from "@/components/ui/SelectField";
import { Spinner } from "@/components/ui/Spinner";
import { Menu, MenuItem, MenuSeparator } from "@/components/ui/Menu";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { PlusIcon } from "@/components/ui/icons";
import { cn } from "@/lib/cn";
import {
  MODELS_KEY,
  POLICIES_KEY,
  PROFILES_KEY,
  QUEUES_KEY,
  TASK_DEFAULTS_KEY,
  errorText,
  invalidateCatalog,
  numberOrNull,
} from "./shared";

/** One line of "Verwendet für". `inherited` = the task has no profile of its
 * own there and runs on the agent's profile. */
type ProfileUse = {
  task: AiTask;
  scope: string | null; // null = global, else queue name
  inherited: boolean;
};

function toFormValues(row: LlmProfileOut | null): FieldValues {
  return row
    ? {
        name: row.name,
        description: row.description ?? "",
        timeout_seconds: row.timeout_seconds ?? "",
        active: row.valid_id === 1,
        llm_model_ids: row.entries.map((e) => e.llm_model_id),
      }
    : { name: "", description: "", timeout_seconds: "", active: true, llm_model_ids: [] };
}

export function ProfilesTab() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { confirm, dialog: confirmDialog } = useConfirm();

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<LlmProfileOut | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const profilesQ = useQuery({
    queryKey: PROFILES_KEY,
    queryFn: ({ signal }) => aiApi.listProfiles(signal),
  });
  const modelsQ = useQuery({
    queryKey: MODELS_KEY,
    queryFn: ({ signal }) => aiApi.listModels(signal),
  });
  // Only for the inherited uses (triage/summary/refine on the agent profile),
  // which the API's `used_by` does not list.
  const defaultsQ = useQuery({
    queryKey: TASK_DEFAULTS_KEY,
    queryFn: ({ signal }) => aiApi.getTaskDefaults(signal),
  });
  const policiesQ = useQuery({
    queryKey: POLICIES_KEY,
    queryFn: ({ signal }) => aiApi.listQueuePolicies(signal),
  });
  const queuesQ = useQuery({
    queryKey: QUEUES_KEY,
    queryFn: ({ signal }) => api.listReferenceQueues({}, signal),
  });

  const profiles = useMemo(() => profilesQ.data ?? [], [profilesQ.data]);
  const models = useMemo(() => modelsQ.data ?? [], [modelsQ.data]);
  const modelById = useMemo(() => new Map(models.map((m) => [m.id, m])), [models]);

  const usesByProfile = useMemo(() => {
    const out = new Map<number, ProfileUse[]>();
    const add = (profileId: number, use: ProfileUse) =>
      out.set(profileId, [...(out.get(profileId) ?? []), use]);
    // Direct assignments, straight from the API.
    for (const p of profiles) {
      for (const u of p.used_by) {
        if (!isAiTask(u.task)) continue;
        add(p.id, {
          task: u.task,
          scope: u.queue_policy_id == null ? null : (u.queue_name ?? `#${u.queue_policy_id}`),
          inherited: false,
        });
      }
    }
    // Inherited: a fallback task without a profile of its own runs on the
    // agent's. Global level first, then queues whose result differs from it.
    const active = new Set(profiles.filter((p) => p.valid_id === 1).map((p) => p.id));
    const isActive = (id: number) => active.has(id);
    const globalMap = taskProfileMap(defaultsQ.data);
    const fallbackTasks = AI_TASKS.filter((task) => AI_TASKS_FALLING_BACK_TO_AGENT.has(task));
    const globalResolved = new Map<AiTask, number | null>();
    for (const task of fallbackTasks) {
      const resolved = resolveTaskProfile(task, globalMap, null, isActive);
      globalResolved.set(task, resolved);
      if (resolved != null && globalMap.get(task) !== resolved) {
        add(resolved, { task, scope: null, inherited: true });
      }
    }
    const queueName = new Map((queuesQ.data ?? []).map((q) => [q.id, q.name]));
    for (const policy of policiesQ.data?.items ?? []) {
      const queueMap = taskProfileMap(policy.task_profiles);
      for (const task of fallbackTasks) {
        const resolved = resolveTaskProfile(task, globalMap, queueMap, isActive);
        if (resolved == null || resolved === globalResolved.get(task)) continue;
        if (queueMap.get(task) === resolved) continue; // direct, listed above
        add(resolved, {
          task,
          scope: queueName.get(policy.queue_id) ?? `#${policy.queue_id}`,
          inherited: true,
        });
      }
    }
    return out;
  }, [profiles, defaultsQ.data, policiesQ.data, queuesQ.data]);

  const saveM = useMutation({
    mutationFn: ({ id, body }: { id: number | null; body: LlmProfileIn }) =>
      id == null ? aiApi.createProfile(body) : aiApi.updateProfile(id, body),
    onSuccess: async () => {
      setDrawerOpen(false);
      await invalidateCatalog(qc);
    },
  });

  const deleteM = useMutation({
    mutationFn: (id: number) => aiApi.deleteProfile(id),
    onMutate: () => setActionError(null),
    onSuccess: () => invalidateCatalog(qc),
    onError: (err) => setActionError(errorText(err, t("admin.form.genericError"))),
  });

  const openCreate = () => {
    setEditing(null);
    setFormError(null);
    setDrawerOpen(true);
  };
  const openEdit = (row: LlmProfileOut) => {
    setEditing(row);
    setFormError(null);
    setDrawerOpen(true);
  };

  const handleSubmit = async (values: FieldValues) => {
    setFormError(null);
    const ids = Array.isArray(values.llm_model_ids) ? (values.llm_model_ids as number[]) : [];
    if (ids.length === 0) {
      const msg = t("admin.ai.profiles.needOneModel");
      setFormError(msg);
      return;
    }
    const body: LlmProfileIn = {
      name: String(values.name ?? "").trim(),
      description: String(values.description ?? "").trim() || null,
      timeout_seconds: numberOrNull(values.timeout_seconds) || null,
      valid_id: values.active ? 1 : 2,
      llm_model_ids: ids,
    };
    try {
      await saveM.mutateAsync({ id: editing?.id ?? null, body });
    } catch (err) {
      // Drawer stays open (it only closes on success) with the server's
      // message; no rethrow — CrudDrawer calls onSubmit fire-and-forget.
      setFormError(errorText(err, t("admin.form.genericError")));
    }
  };

  const handleDelete = async (row: LlmProfileOut) => {
    const ok = await confirm({
      title: t("admin.ai.profiles.title"),
      message: t("admin.ai.profiles.deleteConfirm", { name: row.name }),
      variant: "danger",
    });
    if (ok) deleteM.mutate(row.id);
  };

  const modelLine = (m: { label: string; provider: string; modelId: string }) => (
    <span className="min-w-0">
      <span className="text-ink">
        {m.label} <span className="text-muted">@ {m.provider}</span>
      </span>
      <small className="block break-all font-mono text-[11px] text-muted">{m.modelId}</small>
    </span>
  );

  const renderEntriesEditor = (value: unknown, onChange: (v: unknown) => void) => {
    const ids = Array.isArray(value) ? (value as number[]) : [];
    const move = (from: number, to: number) => {
      const next = [...ids];
      const [item] = next.splice(from, 1);
      next.splice(to, 0, item);
      onChange(next);
    };
    const addable = models.filter((m) => !ids.includes(m.id));
    return (
      <div className="space-y-2" data-testid="admin-ai-profile-form-llm_model_ids">
        {ids.length === 0 ? (
          <p className="text-xs text-muted">{t("admin.ai.profiles.noModelsYet")}</p>
        ) : (
          <ol className="space-y-1">
            {ids.map((id, i) => {
              const m: LlmModelOut | undefined = modelById.get(id);
              return (
                <li
                  key={id}
                  data-testid={`admin-ai-profile-entry-${id}`}
                  className="grid grid-cols-[1.5rem_minmax(0,1fr)_auto] items-center gap-2 rounded-md bg-surface-subtle px-2 py-1.5 text-sm"
                >
                  <span className="text-center font-mono text-[11px] text-muted">{i + 1}</span>
                  {modelLine({
                    label: m?.label ?? `#${id}`,
                    provider: m?.provider_name ?? "?",
                    modelId: m?.model_id ?? "",
                  })}
                  <span className="flex items-center gap-0.5">
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={i === 0}
                      onClick={() => move(i, i - 1)}
                      aria-label={t("admin.ai.profiles.moveUp")}
                      title={t("admin.ai.profiles.moveUp")}
                      data-testid={`admin-ai-profile-entry-up-${id}`}
                    >
                      ↑
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={i === ids.length - 1}
                      onClick={() => move(i, i + 1)}
                      aria-label={t("admin.ai.profiles.moveDown")}
                      title={t("admin.ai.profiles.moveDown")}
                      data-testid={`admin-ai-profile-entry-down-${id}`}
                    >
                      ↓
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => onChange(ids.filter((x) => x !== id))}
                      aria-label={t("admin.ai.profiles.remove")}
                      title={t("admin.ai.profiles.remove")}
                      data-testid={`admin-ai-profile-entry-remove-${id}`}
                    >
                      ✕
                    </Button>
                  </span>
                </li>
              );
            })}
          </ol>
        )}
        {addable.length > 0 && (
          <SelectField<number>
            testId="admin-ai-profile-add-model"
            items={addable.map((m) => ({
              value: m.id,
              label: `${m.label} @ ${m.provider_name}`,
              hint: m.valid_id === 1 ? undefined : t("admin.ai.models.inactive"),
            }))}
            value={null}
            onChange={(id) => onChange([...ids, id])}
            placeholder={t("admin.ai.profiles.addModel")}
          />
        )}
      </div>
    );
  };

  const fields: FieldDef[] = [
    { name: "name", label: t("admin.ai.profiles.name"), type: "text", required: true },
    {
      name: "description",
      label: t("admin.ai.profiles.descriptionField"),
      type: "textarea",
      rows: 2,
    },
    {
      name: "llm_model_ids",
      label: t("admin.ai.profiles.models"),
      type: "custom",
      helpText: t("admin.ai.profiles.modelsHelp"),
      render: (value, onChange) => renderEntriesEditor(value, onChange),
    },
    {
      name: "timeout_seconds",
      label: t("admin.ai.profiles.timeout"),
      type: "number",
      helpText: t("admin.ai.profiles.timeoutHelp"),
    },
    { name: "active", label: t("admin.ai.profiles.active"), type: "checkbox" },
  ];

  const labelOfUse = (u: ProfileUse) => {
    const scope = u.scope == null ? t("admin.ai.profiles.scopeGlobal") : u.scope;
    return `${t(aiTaskNameKey(u.task))} (${scope})`;
  };

  const renderCard = (p: LlmProfileOut) => {
    const uses = usesByProfile.get(p.id) ?? [];
    return (
      <article
        key={p.id}
        data-testid={`admin-ai-profile-card-${p.id}`}
        className={cn(
          "grid min-w-0 content-start gap-2.5 rounded-xl border border-hairline bg-surface px-3.5 py-3",
          p.valid_id !== 1 && "opacity-70",
        )}
      >
        <header className="flex items-baseline justify-between gap-2">
          <div className="flex min-w-0 items-baseline gap-2">
            <b className="truncate text-sm text-ink">{p.name}</b>
            {p.valid_id !== 1 && <Badge tone="muted">{t("admin.ai.profiles.inactive")}</Badge>}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <span className="text-xs text-muted">
              {t("admin.ai.profiles.modelCount", { count: p.entries.length })}
            </span>
            <Menu
              panelTestId={`admin-ai-profile-menu-${p.id}`}
              trigger={({ ref, toggleProps }) => (
                <button
                  type="button"
                  ref={ref}
                  {...toggleProps}
                  data-testid={`admin-ai-profile-menu-trigger-${p.id}`}
                  title={t("admin.table.actions")}
                  className="rounded-md px-1.5 py-1 text-sm leading-none text-muted transition-colors hover:bg-surface-subtle hover:text-ink"
                >
                  ⋯
                </button>
              )}
            >
              <MenuItem testId={`admin-ai-profile-edit-${p.id}`} onSelect={() => openEdit(p)}>
                {t("admin.table.edit")}
              </MenuItem>
              <MenuSeparator />
              <MenuItem
                danger
                testId={`admin-ai-profile-delete-${p.id}`}
                onSelect={() => void handleDelete(p)}
              >
                {t("admin.table.delete")}
              </MenuItem>
            </Menu>
          </div>
        </header>
        {p.description && <p className="text-xs text-muted">{p.description}</p>}
        <ol className="grid gap-1">
          {p.entries.map((e, i) => (
            <li
              key={e.llm_model_id}
              className={cn(
                "grid grid-cols-[1.25rem_minmax(0,1fr)_auto] items-center gap-2 rounded-md bg-surface-subtle px-2 py-1.5 text-[12.5px]",
                e.valid_id !== 1 && "opacity-60",
              )}
            >
              <span className="text-center font-mono text-[11px] text-muted">{i + 1}</span>
              {modelLine({ label: e.model_label, provider: e.provider_name, modelId: e.model_id })}
              {e.valid_id !== 1 ? (
                <Badge tone="muted" title={t("admin.ai.profiles.modelSkipped")}>
                  {t("admin.ai.models.inactive")}
                </Badge>
              ) : (
                <span />
              )}
            </li>
          ))}
        </ol>
        <p className="text-xs text-muted">
          {p.timeout_seconds
            ? t("admin.ai.profiles.timeoutValue", { seconds: p.timeout_seconds })
            : t("admin.ai.profiles.timeoutGlobal")}
        </p>
        <div className="text-xs" data-testid={`admin-ai-profile-used-${p.id}`}>
          <span className="text-muted">{t("admin.ai.profiles.usedBy")}: </span>
          {uses.length === 0 ? (
            <span className="text-muted">{t("admin.ai.profiles.usedByNone")}</span>
          ) : (
            <ul className="mt-1 grid gap-0.5">
              {uses.map((u) => (
                <li
                  key={`${u.task}-${u.scope ?? ""}-${u.inherited}`}
                  className={u.inherited ? "text-muted" : "text-ink"}
                >
                  {labelOfUse(u)}
                  {u.inherited && (
                    <span className="ml-1">
                      · {t("admin.ai.profiles.inheritedVia", { task: t(aiTaskNameKey("agent")) })}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </article>
    );
  };

  return (
    <div className="space-y-3" data-testid="admin-ai-profiles-tab">
      <div className="flex items-start justify-between gap-3">
        <p className="max-w-3xl text-xs text-muted">{t("admin.ai.profiles.intro")}</p>
        <Button
          variant="primary"
          size="sm"
          data-testid="admin-ai-profiles-new"
          onClick={openCreate}
          disabled={models.length === 0}
          className="shrink-0"
        >
          <PlusIcon className="text-[16px]" />
          {t("admin.ai.profiles.new")}
        </Button>
      </div>
      {actionError && (
        <p className="text-sm text-danger" data-testid="admin-ai-profiles-error">
          {actionError}
        </p>
      )}
      {profilesQ.isLoading ? (
        <div className="flex justify-center p-6">
          <Spinner />
        </div>
      ) : profiles.length === 0 ? (
        <p className="rounded-xl border border-hairline bg-surface p-4 text-sm text-muted">
          {t("admin.ai.profiles.empty")}
        </p>
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(290px,1fr))] gap-3">
          {profiles.map(renderCard)}
        </div>
      )}

      <CrudDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        title={
          editing
            ? t("admin.form.editTitle", { title: editing.name })
            : t("admin.ai.profiles.new")
        }
        fields={fields}
        mode={editing ? "edit" : "create"}
        initialValues={toFormValues(editing)}
        onSubmit={handleSubmit}
        submitError={formError}
        testIdPrefix="admin-ai-profile-form"
      />
      {confirmDialog}
    </div>
  );
}
