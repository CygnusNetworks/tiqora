import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { Spinner } from "@/components/ui/Spinner";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";

/**
 * Template↔Attachments assignment editor: pick a standard template, multi-select
 * its attachments, then Save. Reads GET /admin/templates/{id}/attachments and
 * writes the full set via PUT replace (attachment_ids).
 */
export function TemplateAttachmentsPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [templateId, setTemplateId] = useState<number | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());

  const templatesQ = useQuery({
    queryKey: ["admin", "templates", "ref"],
    queryFn: () => api.adminTemplates.list({ valid: "valid", pageSize: 500 }),
    staleTime: 5 * 60 * 1000,
  });

  const attachmentsQ = useQuery({
    queryKey: ["admin", "attachments", "ref"],
    queryFn: () => api.adminAttachments.list({ valid: "valid", pageSize: 500 }),
    staleTime: 5 * 60 * 1000,
  });

  const assignedQ = useQuery({
    queryKey: ["admin", "template-attachments", templateId],
    queryFn: () => api.listTemplateAttachments(templateId as number),
    enabled: templateId !== null,
  });

  useEffect(() => {
    if (assignedQ.data) {
      setSelected(new Set(assignedQ.data.map((a) => a.id)));
    } else if (templateId === null) {
      setSelected(new Set());
    }
  }, [assignedQ.data, templateId]);

  const saveM = useMutation({
    mutationFn: () =>
      api.replaceTemplateAttachments(templateId as number, {
        attachment_ids: Array.from(selected),
      }),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: ["admin", "template-attachments", templateId],
      }),
  });

  const attachments = attachmentsQ.data?.items ?? [];
  const dirty =
    templateId !== null &&
    assignedQ.data !== undefined &&
    !setsEqual(selected, new Set(assignedQ.data.map((a) => a.id)));

  return (
    <div className="space-y-4 p-4" data-testid="admin-template-attachments-page">
      <div>
        <h1 className="font-display text-xl font-semibold text-ink">
          {t("admin.templateAttachments.title")}
        </h1>
        <p className="mt-1 text-sm text-muted">{t("admin.templateAttachments.subtitle")}</p>
      </div>

      <label className="block max-w-md space-y-1">
        <span className="text-xs font-medium uppercase tracking-wide text-muted">
          {t("admin.templateAttachments.template")}
        </span>
        <select
          className="w-full rounded-lg border border-hairline bg-surface px-3 py-2 text-sm text-ink"
          data-testid="admin-template-attachments-select"
          value={templateId ?? ""}
          onChange={(e) => {
            const v = e.currentTarget.value;
            setTemplateId(v ? Number(v) : null);
          }}
        >
          <option value="">{t("admin.templateAttachments.selectTemplate")}</option>
          {(templatesQ.data?.items ?? []).map((tmpl) => (
            <option key={tmpl.id} value={tmpl.id}>
              {tmpl.name}
            </option>
          ))}
        </select>
      </label>

      {templateId === null ? (
        <p className="rounded-lg border border-dashed border-hairline bg-surface px-4 py-8 text-center text-sm text-muted">
          {t("admin.templateAttachments.empty")}
        </p>
      ) : (
        <div className="rounded-lg border border-hairline bg-surface">
          <div className="flex items-center justify-between border-b border-hairline px-4 py-2 text-xs uppercase tracking-wide text-muted">
            <span>{t("admin.templateAttachments.attachments")}</span>
            <div className="flex items-center gap-2">
              {(assignedQ.isFetching || saveM.isPending) && <Spinner className="size-4" />}
              <Button
                type="button"
                variant="primary"
                size="sm"
                disabled={saveM.isPending || assignedQ.isLoading || !dirty}
                data-testid="admin-template-attachments-save"
                onClick={() => saveM.mutate()}
              >
                {saveM.isPending
                  ? t("admin.form.saving")
                  : t("admin.templateAttachments.save")}
              </Button>
            </div>
          </div>
          <ul className="divide-y divide-hairline">
            {attachments.map((att) => {
              const checked = selected.has(att.id);
              return (
                <li
                  key={att.id}
                  className="flex items-center justify-between px-4 py-2.5"
                  data-testid={`admin-template-attachment-row-${att.id}`}
                >
                  <label className="flex items-center gap-3">
                    <input
                      type="checkbox"
                      className="size-4 accent-accent"
                      checked={checked}
                      disabled={saveM.isPending}
                      data-testid={`admin-template-attachment-toggle-${att.id}`}
                      onChange={(e) => {
                        setSelected((prev) => {
                          const next = new Set(prev);
                          if (e.target.checked) next.add(att.id);
                          else next.delete(att.id);
                          return next;
                        });
                      }}
                    />
                    <span className="text-sm text-ink">
                      {att.name}
                      <span className="ml-2 text-xs text-muted">{att.filename}</span>
                    </span>
                  </label>
                  {checked && (
                    <Badge tone="success">{t("admin.templateAttachments.assigned")}</Badge>
                  )}
                </li>
              );
            })}
            {attachments.length === 0 && (
              <li className="px-4 py-6 text-center text-sm text-muted">
                {t("admin.templateAttachments.noAttachments")}
              </li>
            )}
          </ul>
          {saveM.isError && (
            <p className="border-t border-hairline px-4 py-2 text-sm text-escalation">
              {t("admin.templateAttachments.saveError")}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function setsEqual(a: Set<number>, b: Set<number>): boolean {
  if (a.size !== b.size) return false;
  for (const id of a) if (!b.has(id)) return false;
  return true;
}
