import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/lib/api";
import {
  aiApi,
  type McpClientCreate,
  type McpClientOut,
  type McpClientUpdate,
  type McpDiscoverOut,
} from "@/lib/aiApi";
import { CrudDrawer, type FieldDef, type FieldValues } from "@/components/admin/CrudDrawer";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Spinner } from "@/components/ui/Spinner";
import { PlusIcon, ChevronDownIcon } from "@/components/ui/icons";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/cn";

const QUERY_KEY = ["admin", "ai", "mcp-clients"] as const;

function toFormValues(row: McpClientOut | null): FieldValues {
  return row
    ? { name: row.name, url: row.url, auth_token: "" }
    : { name: "", url: "", auth_token: "" };
}

function ClientTools({ clientId }: { clientId: number }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const toolsKey = ["admin", "ai", "mcp-clients", clientId, "tools"] as const;

  const toolsQ = useQuery({
    queryKey: toolsKey,
    queryFn: ({ signal }) => aiApi.listMcpToolPolicies(clientId, signal),
  });

  const toggleM = useMutation({
    mutationFn: ({
      toolName,
      patch,
    }: {
      toolName: string;
      patch: { enabled?: boolean; mutating?: boolean };
    }) => aiApi.updateMcpToolPolicy(clientId, toolName, patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: toolsKey }),
  });

  if (toolsQ.isLoading) {
    return (
      <div className="flex items-center gap-2 p-2 text-xs text-muted">
        <Spinner className="h-3 w-3" /> {t("admin.ai.mcp.toolsLoading")}
      </div>
    );
  }

  const tools = toolsQ.data ?? [];
  if (tools.length === 0) {
    return <p className="p-2 text-xs text-muted">{t("admin.ai.mcp.noTools")}</p>;
  }

  return (
    <ul className="divide-y divide-hairline" data-testid={`admin-ai-mcp-tools-${clientId}`}>
      {tools.map((tool) => (
        <li key={tool.id} className="flex flex-wrap items-center justify-between gap-2 px-2 py-2">
          <div className="min-w-0">
            <p className="font-mono text-xs text-ink">{tool.tool_name}</p>
            {tool.description_snapshot && (
              <p className="max-w-md truncate text-xs text-muted">{tool.description_snapshot}</p>
            )}
          </div>
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-1.5 text-xs text-muted">
              <input
                type="checkbox"
                data-testid={`admin-ai-mcp-tool-mutating-${clientId}-${tool.tool_name}`}
                checked={tool.mutating}
                onChange={(e) =>
                  toggleM.mutate({ toolName: tool.tool_name, patch: { mutating: e.target.checked } })
                }
                className="rounded border-hairline"
              />
              {t("admin.ai.mcp.mutating")}
            </label>
            <label className="flex items-center gap-1.5 text-xs text-ink">
              <input
                type="checkbox"
                data-testid={`admin-ai-mcp-tool-enabled-${clientId}-${tool.tool_name}`}
                checked={tool.enabled}
                onChange={(e) =>
                  toggleM.mutate({ toolName: tool.tool_name, patch: { enabled: e.target.checked } })
                }
                className="rounded border-hairline"
              />
              {t("admin.ai.mcp.enabled")}
            </label>
          </div>
        </li>
      ))}
    </ul>
  );
}

export function AiMcpClientsPage() {
  const { t, i18n } = useTranslation();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";
  const qc = useQueryClient();

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<McpClientOut | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [discoverResult, setDiscoverResult] = useState<Record<number, McpDiscoverOut | string>>({});
  const [discoveringId, setDiscoveringId] = useState<number | null>(null);

  const listQ = useQuery({
    queryKey: QUERY_KEY,
    queryFn: ({ signal }) => aiApi.listMcpClients(signal),
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: QUERY_KEY });

  const createM = useMutation({
    mutationFn: (body: McpClientCreate) => aiApi.createMcpClient(body),
    onSuccess: async () => {
      setDrawerOpen(false);
      await invalidate();
    },
  });

  const updateM = useMutation({
    mutationFn: ({ id, body }: { id: number; body: McpClientUpdate }) =>
      aiApi.updateMcpClient(id, body),
    onSuccess: async () => {
      setDrawerOpen(false);
      await invalidate();
    },
  });

  const deleteM = useMutation({
    mutationFn: (id: number) => aiApi.deleteMcpClient(id),
    onSuccess: () => invalidate(),
  });

  const discoverM = useMutation({
    mutationFn: (id: number) => aiApi.discoverMcpTools(id),
    onMutate: (id) => setDiscoveringId(id),
    onSuccess: async (result, id) => {
      setDiscoverResult((r) => ({ ...r, [id]: result }));
      setExpanded((s) => new Set(s).add(id));
      await qc.invalidateQueries({ queryKey: ["admin", "ai", "mcp-clients", id, "tools"] });
      await invalidate();
    },
    onError: (err, id) =>
      setDiscoverResult((r) => ({
        ...r,
        [id]: err instanceof ApiError ? err.message : String(err),
      })),
    onSettled: () => setDiscoveringId(null),
  });

  const openCreate = () => {
    setEditing(null);
    setFormError(null);
    setDrawerOpen(true);
  };
  const openEdit = (row: McpClientOut) => {
    setEditing(row);
    setFormError(null);
    setDrawerOpen(true);
  };

  const handleSubmit = async (values: FieldValues) => {
    setFormError(null);
    const base = { name: String(values.name ?? ""), url: String(values.url ?? "") };
    const token = typeof values.auth_token === "string" ? values.auth_token.trim() : "";
    try {
      if (editing) {
        const body: McpClientUpdate = { ...base };
        if (token) body.auth_token = token;
        await updateM.mutateAsync({ id: editing.id, body });
      } else {
        await createM.mutateAsync({ ...base, auth_token: token || null });
      }
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : t("admin.form.genericError"));
      throw err;
    }
  };

  const fields: FieldDef[] = [
    { name: "name", label: t("admin.ai.mcp.name"), type: "text", required: true },
    { name: "url", label: t("admin.ai.mcp.url"), type: "text", required: true },
    {
      name: "auth_token",
      label: t("admin.ai.mcp.authToken"),
      type: "password",
      helpText: editing?.has_auth_token
        ? t("admin.ai.mcp.authTokenSetHelp")
        : t("admin.ai.mcp.authTokenHelp"),
    },
  ];

  const clients = listQ.data?.items ?? [];

  return (
    <div className="space-y-3 p-4" data-testid="admin-ai-mcp-page">
      <div className="flex items-center justify-between gap-3">
        <h1 className="font-display text-xl font-semibold text-ink">{t("admin.ai.mcp.title")}</h1>
        <Button
          variant="primary"
          size="sm"
          data-testid="admin-ai-mcp-new"
          onClick={openCreate}
          aria-label={t("admin.ai.mcp.new")}
          title={t("admin.ai.mcp.new")}
          className="!px-2"
        >
          <PlusIcon className="text-[16px]" />
        </Button>
      </div>
      <p className="text-xs text-muted">{t("admin.ai.mcp.description")}</p>

      {listQ.isLoading ? (
        <div className="flex items-center gap-2 p-4">
          <Spinner />
        </div>
      ) : clients.length === 0 ? (
        <p className="p-4 text-sm text-muted">{t("admin.ai.mcp.empty")}</p>
      ) : (
        <div className="space-y-2" data-testid="admin-ai-mcp-list">
          {clients.map((c) => {
            const isOpen = expanded.has(c.id);
            const result = discoverResult[c.id];
            return (
              <div
                key={c.id}
                className="rounded-lg border border-hairline bg-surface"
                data-testid={`admin-ai-mcp-row-${c.id}`}
              >
                <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
                  <button
                    type="button"
                    className="flex min-w-0 flex-1 items-center gap-2 text-left"
                    data-testid={`admin-ai-mcp-toggle-${c.id}`}
                    onClick={() =>
                      setExpanded((s) => {
                        const next = new Set(s);
                        if (next.has(c.id)) next.delete(c.id);
                        else next.add(c.id);
                        return next;
                      })
                    }
                  >
                    <ChevronDownIcon className={cn("shrink-0 transition-transform", isOpen && "rotate-180")} />
                    <div className="min-w-0">
                      <p className="truncate font-medium text-ink">{c.name}</p>
                      <p className="truncate font-mono text-xs text-muted">{c.url}</p>
                    </div>
                  </button>
                  <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
                    {c.valid_id !== 1 && <Badge tone="muted">{t("admin.table.invalid")}</Badge>}
                    {c.has_auth_token && <Badge tone="muted">{t("admin.ai.mcp.hasToken")}</Badge>}
                    <span data-testid={`admin-ai-mcp-last-discovered-${c.id}`}>
                      {c.last_discovered_at
                        ? t("admin.ai.mcp.lastDiscovered", {
                            date: formatDateTime(c.last_discovered_at, locale),
                          })
                        : t("admin.ai.mcp.neverDiscovered")}
                    </span>
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={discoveringId === c.id}
                      data-testid={`admin-ai-mcp-discover-${c.id}`}
                      onClick={() => discoverM.mutate(c.id)}
                    >
                      {discoveringId === c.id ? (
                        <Spinner className="h-3 w-3" />
                      ) : (
                        t("admin.ai.mcp.discover")
                      )}
                    </Button>
                    <Button size="sm" variant="secondary" onClick={() => openEdit(c)}>
                      {t("admin.table.edit")}
                    </Button>
                    <Button
                      size="sm"
                      variant="danger"
                      onClick={() => {
                        if (window.confirm(t("admin.ai.mcp.deleteConfirm", { name: c.name }))) {
                          deleteM.mutate(c.id);
                        }
                      }}
                    >
                      {t("admin.table.delete")}
                    </Button>
                  </div>
                </div>
                {typeof result === "string" && (
                  <p className="border-t border-hairline px-3 py-1.5 text-xs text-danger">{result}</p>
                )}
                {result && typeof result !== "string" && (
                  <p
                    className="border-t border-hairline px-3 py-1.5 text-xs text-green"
                    data-testid={`admin-ai-mcp-discover-result-${c.id}`}
                  >
                    {t("admin.ai.mcp.discoverResult", {
                      added: result.added.length,
                      removed: result.removed.length,
                      total: result.tool_names.length,
                    })}
                  </p>
                )}
                {isOpen && (
                  <div className="border-t border-hairline">
                    <ClientTools clientId={c.id} />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      <CrudDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        title={
          editing ? t("admin.form.editTitle", { title: t("admin.ai.mcp.title") }) : t("admin.ai.mcp.new")
        }
        fields={fields}
        mode={editing ? "edit" : "create"}
        initialValues={toFormValues(editing)}
        onSubmit={handleSubmit}
        submitError={formError}
        testIdPrefix="admin-ai-mcp-form"
      />
    </div>
  );
}
