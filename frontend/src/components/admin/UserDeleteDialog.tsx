import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { api, ApiError, type UserOut } from "@/lib/api";

/**
 * Permanent delete, as opposed to the list's "deactivate" (a soft
 * `valid_id = 2`). Znuny's schema points foreign keys at `users.id` from
 * nearly every table, so this is only possible for an account that was never
 * used — the dialog asks the backend first and, when refused, names the
 * tables that still reference the agent instead of offering a button that
 * would fail.
 */
export function UserDeleteDialog({
  user,
  onClose,
  onDeleted,
}: {
  user: UserOut | null;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const open = user !== null;

  const checkQ = useQuery({
    queryKey: ["admin", "users", user?.id, "deletable"],
    queryFn: ({ signal }) => api.getUserDeletable(user!.id, signal),
    enabled: open,
  });

  const deleteM = useMutation({
    mutationFn: () => api.deleteUserPermanently(user!.id),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["admin", "users"] });
      onDeleted();
    },
  });

  const name = user ? `${user.first_name} ${user.last_name}`.trim() || user.login : "";
  const blocking = checkQ.data?.blocking ?? [];
  const deletable = checkQ.data?.deletable === true;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t("admin.users.deleteTitle", { name })}
      size="md"
      footer={
        <>
          {deleteM.isError && (
            <p className="mr-auto text-sm text-escalation" data-testid="user-delete-error">
              {deleteM.error instanceof ApiError
                ? deleteM.error.message
                : String(deleteM.error)}
            </p>
          )}
          <Button variant="ghost" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button
            variant="danger"
            disabled={!deletable || deleteM.isPending}
            data-testid="user-delete-confirm"
            onClick={() => deleteM.mutate()}
          >
            {deleteM.isPending ? t("admin.form.saving") : t("admin.users.deleteConfirm")}
          </Button>
        </>
      }
    >
      {checkQ.isLoading ? (
        <div className="flex justify-center py-6">
          <Spinner />
        </div>
      ) : deletable ? (
        <div className="space-y-2" data-testid="user-delete-deletable">
          <p className="text-sm text-ink">{t("admin.users.deletableBody", { name })}</p>
          <p className="text-xs text-muted">{t("admin.users.deleteIrreversible")}</p>
        </div>
      ) : (
        <div className="space-y-2" data-testid="user-delete-blocked">
          <p className="text-sm text-ink">{t("admin.users.deleteBlockedBody", { name })}</p>
          <ul className="max-h-48 space-y-0.5 overflow-y-auto rounded-md border border-hairline bg-surface-subtle p-2">
            {blocking.map((r) => (
              <li key={`${r.table}.${r.column}`} className="font-mono text-xs text-muted">
                {r.table}.{r.column}
              </li>
            ))}
          </ul>
          <p className="text-xs text-muted">{t("admin.users.deleteBlockedHint")}</p>
        </div>
      )}
    </Dialog>
  );
}
