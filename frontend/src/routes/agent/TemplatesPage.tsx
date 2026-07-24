import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type StandardTemplateOut, type StandardTemplateUpdate } from "@/lib/api";
import { useAuth } from "@/auth/AuthContext";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Spinner } from "@/components/ui/Spinner";
import { formatDateTime } from "@/lib/format";

const LIST_KEY = ["agent", "templates"] as const;

/**
 * Agent-facing template editing: lists only the Standard Templates the agent
 * has been granted (per-template ACL) and lets them edit name/body/comment.
 * Managing WHO may edit stays in the admin console.
 */
export function TemplatesPage() {
  const { t, i18n } = useTranslation();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";
  const { user } = useAuth();
  const qc = useQueryClient();
  const [editing, setEditing] = useState<StandardTemplateOut | null>(null);

  const listQ = useQuery({
    queryKey: LIST_KEY,
    queryFn: ({ signal }) => api.agentTemplates.list({ valid: "valid", pageSize: 500 }, signal),
    enabled: Boolean(user?.can_edit_templates),
  });

  const rows = useMemo(() => listQ.data?.items ?? [], [listQ.data]);

  if (!user?.can_edit_templates) {
    return (
      <div
        className="m-6 rounded-lg border border-hairline bg-surface p-8 text-center"
        data-testid="templates-access-denied"
      >
        <h1 className="font-display text-lg font-semibold text-ink">
          {t("agentTemplates.deniedTitle")}
        </h1>
        <p className="mt-2 text-sm text-muted">{t("agentTemplates.deniedBody")}</p>
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-5xl space-y-4 p-4" data-testid="agent-templates-page">
      <div>
        <h1 className="font-display text-lg font-semibold text-ink">
          {t("agentTemplates.title")}
        </h1>
        <p className="text-sm text-muted">{t("agentTemplates.subtitle")}</p>
      </div>

      {listQ.isLoading ? (
        <div className="flex justify-center py-10">
          <Spinner />
        </div>
      ) : rows.length === 0 ? (
        <p
          className="rounded-lg border border-hairline bg-surface p-6 text-sm text-muted"
          data-testid="agent-templates-empty"
        >
          {t("agentTemplates.empty")}
        </p>
      ) : (
        <div className="overflow-hidden rounded-lg border border-hairline">
          <table className="w-full text-sm">
            <thead className="bg-surface-subtle text-left text-xs uppercase tracking-wide text-muted">
              <tr>
                <th className="px-3 py-2 font-medium">{t("agentTemplates.name")}</th>
                <th className="px-3 py-2 font-medium">{t("agentTemplates.type")}</th>
                <th className="px-3 py-2 font-medium">{t("admin.table.changed")}</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr
                  key={row.id}
                  className="border-t border-hairline"
                  data-testid={`template-row-${row.id}`}
                >
                  <td className="px-3 py-2 font-medium text-ink">{row.name}</td>
                  <td className="px-3 py-2 text-muted">{row.template_type}</td>
                  <td className="px-3 py-2 tabular-nums text-muted">
                    {formatDateTime(row.change_time, locale)}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <Button
                      size="sm"
                      variant="secondary"
                      data-testid={`template-edit-${row.id}`}
                      onClick={() => setEditing(row)}
                    >
                      {t("common.edit")}
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {editing && (
        <EditTemplateDialog
          template={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            void qc.invalidateQueries({ queryKey: LIST_KEY });
          }}
        />
      )}
    </div>
  );
}

function EditTemplateDialog({
  template,
  onClose,
  onSaved,
}: {
  template: StandardTemplateOut;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState(template.name);
  const [comments, setComments] = useState(template.comments ?? "");
  const [text, setText] = useState(template.text ?? "");

  const saveM = useMutation({
    mutationFn: () => {
      const body: StandardTemplateUpdate = { name, comments: comments || null, text };
      return api.agentTemplates.update(template.id, body);
    },
    onSuccess: onSaved,
  });

  const inputClass =
    "w-full rounded-md border border-hairline bg-surface-subtle px-3 py-2 text-sm text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";

  return (
    <Dialog
      open
      onClose={onClose}
      title={t("agentTemplates.editTitle", { name: template.name })}
      size="lg"
      footer={
        <div className="flex justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={onClose} disabled={saveM.isPending}>
            {t("common.cancel")}
          </Button>
          <Button
            variant="primary"
            size="sm"
            data-testid="template-save"
            disabled={saveM.isPending || !name.trim()}
            onClick={() => saveM.mutate()}
          >
            {saveM.isPending ? <Spinner className="h-4 w-4" /> : t("common.save")}
          </Button>
        </div>
      }
    >
      <div className="space-y-3" data-testid="template-edit-form">
        <label className="block text-sm">
          <span className="mb-1 block text-muted">{t("agentTemplates.name")}</span>
          <input
            data-testid="template-form-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className={inputClass}
          />
        </label>
        <label className="block text-sm">
          <span className="mb-1 block text-muted">{t("agentTemplates.comment")}</span>
          <input
            data-testid="template-form-comment"
            value={comments}
            onChange={(e) => setComments(e.target.value)}
            className={inputClass}
          />
        </label>
        <label className="block text-sm">
          <span className="mb-1 block text-muted">{t("agentTemplates.body")}</span>
          <textarea
            data-testid="template-form-body"
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={12}
            className={`${inputClass} font-mono`}
          />
        </label>
        {saveM.isError && (
          <p className="text-sm text-danger" data-testid="template-save-error">
            {t("agentTemplates.saveError")}
          </p>
        )}
      </div>
    </Dialog>
  );
}
