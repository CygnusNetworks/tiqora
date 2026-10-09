import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { Spinner } from "@/components/ui/Spinner";
import { apiErrorMessage } from "@/lib/avatarApi";
import { formatDateTime } from "@/lib/format";
import { identityApi, type IdentityLink, type IdentityProvider } from "@/lib/identityApi";
import { toBcp47 } from "@/i18n";

/** Accounts that signed in through this provider, with a way to cut the connection. */
export function LinksTab({ provider }: { provider: IdentityProvider }) {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const { confirm, dialog } = useConfirm();
  const locale = toBcp47(i18n.language);

  const linksQ = useQuery({
    queryKey: ["admin", "identity", provider.id, "links"],
    queryFn: ({ signal }) => identityApi.links(provider.id, signal),
  });
  const removeM = useMutation({
    mutationFn: (link: IdentityLink) => identityApi.removeLink(provider.id, link.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "identity"] }),
  });

  const onRemove = async (link: IdentityLink) => {
    const ok = await confirm({
      title: t("admin.identity.links.removeTitle"),
      message: t("admin.identity.links.removeBody", { login: link.user_login }),
      confirmLabel: t("admin.identity.links.remove"),
      variant: "danger",
    });
    if (ok) removeM.mutate(link);
  };

  return (
    <div className="space-y-3" data-testid="identity-links">
      <p className="text-sm text-muted">{t("admin.identity.links.intro")}</p>
      {linksQ.isLoading && <Spinner />}
      {linksQ.isError && (
        <p role="alert" className="text-sm text-danger">
          {apiErrorMessage(linksQ.error, t("admin.identity.links.loadError"))}
        </p>
      )}
      {removeM.isError && (
        <p data-testid="identity-link-remove-error" role="alert" className="text-sm text-danger">
          {apiErrorMessage(removeM.error, t("admin.identity.links.removeError"))}
        </p>
      )}
      {linksQ.data && linksQ.data.length === 0 && (
        <p data-testid="identity-links-empty" className="rounded-lg border border-hairline bg-surface p-4 text-sm text-muted">
          {t("admin.identity.links.empty")}
        </p>
      )}
      {linksQ.data && linksQ.data.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-hairline bg-surface">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="text-xs text-muted">
                <th className="px-3 py-2 font-medium">{t("admin.identity.links.account")}</th>
                <th className="px-3 py-2 font-medium">{t("admin.identity.links.kind")}</th>
                <th className="px-3 py-2 font-medium">{t("admin.identity.links.subject")}</th>
                <th className="px-3 py-2 font-medium">{t("admin.identity.links.firstSeen")}</th>
                <th className="px-3 py-2 font-medium">{t("admin.identity.links.lastSeen")}</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {linksQ.data.map((link) => (
                <tr key={link.id} className="border-t border-hairline" data-testid="identity-link-row">
                  <td className="px-3 py-2 text-ink">{link.user_login}</td>
                  <td className="px-3 py-2 text-ink">{t(`admin.identity.links.kinds.${link.user_kind}`)}</td>
                  <td className="break-all px-3 py-2 font-mono text-[12.5px] text-muted">{link.subject}</td>
                  <td className="px-3 py-2 text-muted">{formatDateTime(link.first_seen, locale)}</td>
                  <td className="px-3 py-2 text-muted">{formatDateTime(link.last_seen, locale)}</td>
                  <td className="px-3 py-2 text-right">
                    <Button
                      type="button"
                      size="sm"
                      data-testid={`identity-link-remove-${link.id}`}
                      disabled={removeM.isPending}
                      onClick={() => onRemove(link)}
                    >
                      {t("admin.identity.links.remove")}
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {dialog}
    </div>
  );
}
