import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  api,
  type StandardTemplateOut,
  type StandardTemplateCreate,
  type StandardTemplateUpdate,
} from "@/lib/api";
import { AdminResourcePage } from "@/components/admin/AdminResourcePage";
import type { FieldDef, FieldValues } from "@/components/admin/CrudDrawer";
import type { DataTableColumn } from "@/components/admin/DataTable";
import { insertTagAtCursor } from "@/components/admin/otrsPlaceholders";
import { VariableReference } from "@/components/admin/VariableReference";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { MenuItem } from "@/components/ui/Menu";
import { Spinner } from "@/components/ui/Spinner";
import { formatDateTime } from "@/lib/format";

const TEMPLATE_TYPE_OPTIONS = [
  { value: "Answer", label: "Answer" },
  { value: "Create", label: "Create" },
  { value: "Note", label: "Note" },
  { value: "Email", label: "Email" },
];

export function TemplatesPage() {
  const { t, i18n } = useTranslation();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";
  const [editorsTarget, setEditorsTarget] = useState<StandardTemplateOut | null>(null);

  const columns: DataTableColumn<StandardTemplateOut>[] = [
    { key: "id", header: t("admin.table.id"), mono: true, render: (r) => r.id },
    { key: "name", header: t("admin.templates.name"), render: (r) => r.name },
    {
      key: "template_type",
      header: t("admin.templates.type"),
      render: (r) => r.template_type,
    },
    {
      key: "assigned_queue_count",
      header: t("admin.templates.usage"),
      render: (r) => {
        const n = r.assigned_queue_count ?? 0;
        return (
          <Badge
            tone={n > 0 ? "default" : "muted"}
            data-testid={`admin-template-usage-${r.id}`}
          >
            {n > 0 ? t("admin.templates.inQueues", { count: n }) : "0"}
          </Badge>
        );
      },
    },
    {
      key: "changed",
      header: t("admin.table.changed"),
      render: (r) => formatDateTime(r.change_time, locale),
    },
  ];

  const fields: FieldDef[] = [
    { name: "name", label: t("admin.templates.name"), type: "text", required: true },
    {
      name: "template_type",
      label: t("admin.templates.type"),
      type: "select",
      options: TEMPLATE_TYPE_OPTIONS,
    },
    // Prose body — proportional UI font (not monospace).
    {
      name: "text",
      label: t("admin.templates.text"),
      type: "textarea",
      mono: false,
      rows: 10,
      afterControl: ({ value, onChange, controlId }) => (
        <VariableReference
          onInsert={(tag) => {
            const el = document.getElementById(controlId) as HTMLTextAreaElement | null;
            const text = typeof value === "string" ? value : "";
            insertTagAtCursor(el, text, tag, (next) => onChange(next));
          }}
        />
      ),
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
    },
  ];

  return (
    <>
    <AdminResourcePage
      resourceKey="templates"
      title={t("admin.templates.title_plural")}
      newLabel={t("admin.templates.new")}
      api={api.adminTemplates}
      idOf={(r) => r.id}
      columns={columns}
      fields={fields}
      rowActions={(row) => (
        <MenuItem
          testId={`admin-template-editors-${row.id}`}
          onSelect={() => setEditorsTarget(row)}
        >
          {t("admin.templates.editors")}
        </MenuItem>
      )}
      toFormValues={(row) =>
        row
          ? {
              name: row.name,
              template_type: row.template_type,
              text: row.text ?? "",
              comments: row.comments ?? "",
              valid_id: row.valid_id,
            }
          : { template_type: "Answer", valid_id: 1 }
      }
      toCreateBody={(v: FieldValues): StandardTemplateCreate => ({
        name: v.name as string,
        template_type: (v.template_type as string) || "Answer",
        text: (v.text as string) || null,
        comments: (v.comments as string) || null,
        valid_id: Number(v.valid_id) || 1,
      })}
      toUpdateBody={(v: FieldValues): StandardTemplateUpdate => ({
        name: v.name as string,
        template_type: (v.template_type as string) || "Answer",
        text: (v.text as string) || null,
        comments: (v.comments as string) || null,
        valid_id: Number(v.valid_id) || 1,
      })}
    />
    {editorsTarget && (
      <TemplateEditorsDialog template={editorsTarget} onClose={() => setEditorsTarget(null)} />
    )}
    </>
  );
}

/** Assign which permission groups + individual agents may edit a template. */
function TemplateEditorsDialog({
  template,
  onClose,
}: {
  template: StandardTemplateOut;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [groupIds, setGroupIds] = useState<Set<number>>(new Set());
  const [userIds, setUserIds] = useState<Set<number>>(new Set());
  const [userFilter, setUserFilter] = useState("");

  const editorsQ = useQuery({
    queryKey: ["admin", "template-editors", template.id],
    queryFn: async ({ signal }) => {
      const out = await api.getTemplateEditors(template.id, signal);
      setGroupIds(new Set(out.group_ids));
      setUserIds(new Set(out.user_ids));
      return out;
    },
  });
  const groupsQ = useQuery({
    queryKey: ["admin", "groups", "ref"],
    queryFn: ({ signal }) => api.adminGroups.list({ valid: "valid", pageSize: 500 }, signal),
  });
  const usersQ = useQuery({
    queryKey: ["admin", "users", "ref"],
    queryFn: ({ signal }) => api.adminUsers.list({ valid: "valid", pageSize: 500 }, signal),
  });

  const saveM = useMutation({
    mutationFn: () =>
      api.setTemplateEditors(template.id, {
        group_ids: Array.from(groupIds),
        user_ids: Array.from(userIds),
      }),
    onSuccess: onClose,
  });

  const toggle = (set: Set<number>, setter: (s: Set<number>) => void, id: number) => {
    const next = new Set(set);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setter(next);
  };

  const users = useMemo(() => {
    const all = usersQ.data?.items ?? [];
    const q = userFilter.trim().toLowerCase();
    if (!q) return all;
    return all.filter(
      (u) =>
        u.login.toLowerCase().includes(q) ||
        `${u.first_name} ${u.last_name}`.toLowerCase().includes(q),
    );
  }, [usersQ.data, userFilter]);

  const loading = editorsQ.isLoading || groupsQ.isLoading || usersQ.isLoading;
  const boxClass =
    "max-h-56 space-y-1 overflow-auto rounded-md border border-hairline bg-surface-subtle p-2";

  return (
    <Dialog
      open
      onClose={onClose}
      title={t("admin.templates.editorsTitle", { name: template.name })}
      description={t("admin.templates.editorsHint")}
      size="lg"
      footer={
        <div className="flex justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={onClose} disabled={saveM.isPending}>
            {t("common.cancel")}
          </Button>
          <Button
            variant="primary"
            size="sm"
            data-testid="template-editors-save"
            disabled={saveM.isPending || loading}
            onClick={() => saveM.mutate()}
          >
            {saveM.isPending ? <Spinner className="h-4 w-4" /> : t("common.save")}
          </Button>
        </div>
      }
    >
      {loading ? (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2" data-testid="template-editors-form">
          <div>
            <p className="mb-1 text-sm font-medium text-ink">{t("admin.templates.editorGroups")}</p>
            <p className="mb-2 text-xs text-muted">{t("admin.templates.editorGroupsHint")}</p>
            <div className={boxClass}>
              {(groupsQ.data?.items ?? []).map((g) => (
                <label key={g.id} className="flex items-center gap-2 text-sm text-ink">
                  <input
                    type="checkbox"
                    data-testid={`template-editor-group-${g.id}`}
                    checked={groupIds.has(g.id)}
                    onChange={() => toggle(groupIds, setGroupIds, g.id)}
                  />
                  {g.name}
                </label>
              ))}
            </div>
          </div>
          <div>
            <p className="mb-1 text-sm font-medium text-ink">{t("admin.templates.editorUsers")}</p>
            <input
              value={userFilter}
              onChange={(e) => setUserFilter(e.target.value)}
              placeholder={t("admin.templates.editorUsersFilter")}
              className="mb-2 w-full rounded-md border border-hairline bg-surface-subtle px-2 py-1 text-sm text-ink"
            />
            <div className={boxClass}>
              {users.map((u) => (
                <label key={u.id} className="flex items-center gap-2 text-sm text-ink">
                  <input
                    type="checkbox"
                    data-testid={`template-editor-user-${u.id}`}
                    checked={userIds.has(u.id)}
                    onChange={() => toggle(userIds, setUserIds, u.id)}
                  />
                  <span className="truncate">
                    {u.first_name} {u.last_name}{" "}
                    <span className="text-muted">({u.login})</span>
                  </span>
                </label>
              ))}
            </div>
          </div>
        </div>
      )}
    </Dialog>
  );
}
