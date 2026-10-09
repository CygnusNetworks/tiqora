import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { PresetDialog } from "@/components/admin/identity/PresetDialog";
import { ProviderIcon } from "@/components/auth/ProviderIcon";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { PlusIcon, TrashIcon } from "@/components/ui/icons";
import { Spinner } from "@/components/ui/Spinner";
import { apiErrorMessage } from "@/lib/avatarApi";
import { identityApi, type IdentityProviderSummary } from "@/lib/identityApi";

export const IDENTITY_LIST_KEY = ["admin", "identity"] as const;

export function IdentityProvidersPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { confirm, dialog } = useConfirm();
  const [dialogOpen, setDialogOpen] = useState(false);

  const listQ = useQuery({
    queryKey: IDENTITY_LIST_KEY,
    queryFn: ({ signal }) => identityApi.list(signal),
  });

  const removeM = useMutation({
    mutationFn: (id: number) => identityApi.remove(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: IDENTITY_LIST_KEY }),
  });

  const askDelete = async (row: IdentityProviderSummary) => {
    const ok = await confirm({
      title: t("admin.identity.deleteTitle"),
      message: t("admin.identity.deleteBody", { name: row.name, count: row.link_count }),
      confirmLabel: t("common.delete"),
      variant: "danger",
    });
    if (ok) removeM.mutate(row.id);
  };

  const rows = listQ.data ?? [];

  return (
    <div className="space-y-4 p-4" data-testid="admin-identity-providers-page">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="font-display text-xl font-semibold text-ink">{t("admin.identity.title")}</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted">{t("admin.identity.description")}</p>
        </div>
        <Button type="button" variant="primary" data-testid="identity-add" onClick={() => setDialogOpen(true)}>
          <PlusIcon className="mr-1 h-4 w-4" />
          {t("admin.identity.add")}
        </Button>
      </div>

      {removeM.isError && (
        <p role="alert" className="text-sm text-danger">
          {apiErrorMessage(removeM.error, t("admin.identity.deleteError"))}
        </p>
      )}

      {listQ.isLoading ? (
        <Spinner />
      ) : listQ.isError ? (
        <p role="alert" className="text-sm text-danger">
          {apiErrorMessage(listQ.error, t("admin.identity.loadError"))}
        </p>
      ) : rows.length === 0 ? (
        <p className="rounded-lg border border-hairline bg-surface p-4 text-sm text-muted" data-testid="identity-empty">
          {t("admin.identity.empty")}
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-hairline bg-surface">
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-muted">
              <tr>
                <th className="px-3 py-2 font-medium">{t("admin.identity.columns.name")}</th>
                <th className="px-3 py-2 font-medium">{t("admin.identity.columns.kind")}</th>
                <th className="px-3 py-2 font-medium">{t("admin.identity.columns.audience")}</th>
                <th className="px-3 py-2 font-medium">{t("admin.identity.columns.state")}</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} data-testid={`identity-row-${row.id}`} className="border-t border-hairline">
                  <td className="px-3 py-2">
                    <Link
                      to="/admin/identity-providers/$providerId"
                      params={{ providerId: String(row.id) }}
                      className="inline-flex items-center gap-2 font-medium text-ink hover:text-accent"
                    >
                      <ProviderIcon icon={row.button_icon} className="h-4 w-4 shrink-0" />
                      {row.name}
                    </Link>
                    {row.source === "env" && (
                      <div>
                        <Badge tone="muted" data-testid="identity-env-badge">
                          {t("admin.identity.envBadge")}
                        </Badge>
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2 text-muted">{t(`admin.identity.kind.${row.kind}`)}</td>
                  <td className="px-3 py-2 text-muted">{t(`admin.identity.audience.${row.audience}`)}</td>
                  <td className="px-3 py-2">
                    <span data-testid={`identity-state-${row.id}`} className="inline-flex flex-wrap gap-1">
                      <Badge tone={row.enabled ? "success" : "muted"}>
                        {row.enabled ? t("admin.identity.state.enabled") : t("admin.identity.state.disabled")}
                      </Badge>
                      {row.metadata_error && <Badge tone="warn">{t("admin.identity.state.metadataError")}</Badge>}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right">
                    {!row.read_only && (
                      <Button
                        type="button"
                        data-testid={`identity-delete-${row.id}`}
                        aria-label={t("admin.identity.deleteAria", { name: row.name })}
                        onClick={() => askDelete(row)}
                      >
                        <TrashIcon className="h-4 w-4" />
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <PresetDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        onCreated={(p) => {
          setDialogOpen(false);
          qc.invalidateQueries({ queryKey: IDENTITY_LIST_KEY });
          navigate({ to: "/admin/identity-providers/$providerId", params: { providerId: String(p.id) } });
        }}
      />
      {dialog}
    </div>
  );
}
