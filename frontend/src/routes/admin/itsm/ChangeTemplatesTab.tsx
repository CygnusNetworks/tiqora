import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { AdminListState } from "@/components/itsm/change/AdminListState";
import { useDeleteConfirm } from "@/components/itsm/change/useDeleteConfirm";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { changeAdminApi, changeAdminKeys, type ChangeTemplate } from "@/lib/changeAdminApi";
import { changeKeys } from "@/lib/changeApi";

const LINK = "rounded-md border border-hairline bg-surface px-2.5 py-1 text-sm text-ink hover:border-accent/50";

/** The change templates (valid or not); edit opens `/admin/itsm/change/templates/$templateId`. */
export function ChangeTemplatesTab() {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: changeAdminKeys.templates(), queryFn: ({ signal }) => changeAdminApi.templates(signal) });
  const del = useDeleteConfirm<ChangeTemplate>({
    remove: (template) => changeAdminApi.deleteTemplate(template.id),
    invalidate: [changeAdminKeys.templates(), changeKeys.templates()],
    title: t("admin.itsmChange.templates.deleteTitle"),
    message: (template) => t("admin.itsmChange.templates.deleteMessage", { name: template.name }),
    errorTestId: "template-delete-error",
  });
  const templates = q.data ?? [];

  return (
    <section className="space-y-3" data-testid="change-templates-tab">
      <div className="flex justify-end">
        <Link
          to="/admin/itsm/change/templates/$templateId"
          params={{ templateId: "new" }}
          className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-ink hover:opacity-90"
          data-testid="template-new"
        >
          {t("admin.itsmChange.templates.new")}
        </Link>
      </div>
      <AdminListState query={q} count={templates.length} emptyText={t("admin.itsmChange.templates.empty")} testId="templates">
          <ul className="divide-y divide-hairline rounded-md border border-hairline bg-surface">
            {templates.map((tpl) => (
              <li key={tpl.id} className="flex flex-wrap items-start justify-between gap-2 px-3 py-2" data-testid="template-row" data-template-id={tpl.id}>
                <div className="min-w-0 flex-1 space-y-0.5">
                  <p className="flex flex-wrap items-center gap-1.5 text-sm font-medium text-ink">
                    <span className="min-w-0 break-words">{tpl.name}</span>
                    <Badge tone="default">{t(`itsm.change.types.${tpl.change_type}`)}</Badge>
                    {tpl.is_standard && (
                      <Badge tone="success" data-testid="template-standard">
                        {t("admin.itsmChange.templates.standard")}
                      </Badge>
                    )}
                    {tpl.broken && (
                      <Badge tone="danger" data-testid="template-broken">
                        {t("admin.itsmChange.templates.broken")}
                      </Badge>
                    )}
                    {!tpl.valid && (
                      <Badge tone="muted" data-testid="template-invalid">
                        {t("admin.itsmChange.invalid")}
                      </Badge>
                    )}
                  </p>
                  {tpl.description && <p className="break-words text-xs text-muted">{tpl.description}</p>}
                  <p className="text-xs text-muted">{t("admin.itsmChange.templates.taskCount", { count: tpl.tasks.length })}</p>
                  {tpl.broken && <p className="text-xs text-danger">{t("admin.itsmChange.templates.brokenHint")}</p>}
                </div>
                <div className="flex gap-2">
                  <Link to="/admin/itsm/change/templates/$templateId" params={{ templateId: String(tpl.id) }} className={LINK} data-testid="template-edit">
                    {t("common.edit")}
                  </Link>
                  <Button size="sm" variant="danger" onClick={() => del.ask(tpl)} data-testid="template-delete">
                    {t("common.delete")}
                  </Button>
                </div>
              </li>
            ))}
          </ul>
      </AdminListState>
      {del.dialog}
    </section>
  );
}
