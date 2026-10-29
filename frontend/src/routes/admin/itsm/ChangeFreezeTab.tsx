import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { changeAdminErrorText } from "@/components/itsm/change/changeAdminUi";
import { windowText } from "@/components/itsm/change/changeUi";
import { FreezeWindowDialog } from "@/components/itsm/change/FreezeWindowDialog";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { api } from "@/lib/api";
import { changeAdminApi, changeAdminKeys, type FreezeWindow } from "@/lib/changeAdminApi";
import { changeKeys } from "@/lib/changeApi";

/** Freeze windows (Sperrzeiträume): effect, scope, UTC window and recurrence; create, edit, delete. */
export function ChangeFreezeTab() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [editor, setEditor] = useState<{ window: FreezeWindow | null } | null>(null);
  const [deleting, setDeleting] = useState<FreezeWindow | null>(null);
  const q = useQuery({ queryKey: changeAdminKeys.freezeWindows(), queryFn: ({ signal }) => changeAdminApi.freezeWindows(signal) });
  const servicesQ = useQuery({
    queryKey: ["reference", "services"],
    queryFn: ({ signal }) => api.listReferenceServices(signal),
    staleTime: 60_000,
  });
  const serviceName = (id: number) => servicesQ.data?.find((s) => s.id === id)?.name ?? `#${id}`;
  const remove = useMutation({
    mutationFn: (w: FreezeWindow) => changeAdminApi.deleteFreezeWindow(w.id),
    onSuccess: () => {
      setDeleting(null);
      void qc.invalidateQueries({ queryKey: changeAdminKeys.freezeWindows() });
      void qc.invalidateQueries({ queryKey: changeKeys.all() });
    },
  });
  const windows = q.data ?? [];

  return (
    <section className="space-y-3" data-testid="change-freeze-tab">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted">{t("admin.itsmChange.freeze.utc")}</p>
        <Button variant="primary" onClick={() => setEditor({ window: null })} data-testid="freeze-new">
          {t("admin.itsmChange.freeze.new")}
        </Button>
      </div>
      {q.isError ? (
        <p className="text-sm text-danger" role="alert" data-testid="freeze-windows-error">
          {changeAdminErrorText(q.error, t)}
        </p>
      ) : windows.length === 0 ? (
        <p className="text-sm text-muted" data-testid="freeze-windows-empty">
          {q.isPending ? t("common.loading") : t("admin.itsmChange.freeze.empty")}
        </p>
      ) : (
        <ul className="divide-y divide-hairline rounded-md border border-hairline bg-surface">
          {windows.map((w) => (
            <li key={w.id} className="flex flex-wrap items-start justify-between gap-2 px-3 py-2" data-testid="freeze-row" data-freeze-id={w.id}>
              <div className="min-w-0 flex-1 space-y-0.5">
                <p className="flex flex-wrap items-center gap-1.5 text-sm font-medium text-ink">
                  <span className="min-w-0 break-words">{w.name}</span>
                  <Badge tone={w.enforcement === "block" ? "danger" : "warn"}>{t(`admin.itsmChange.freeze.enforcement.${w.enforcement}`)}</Badge>
                  {!w.valid && <Badge tone="muted">{t("admin.itsmChange.invalid")}</Badge>}
                </p>
                <p className="font-mono text-xs tabular-nums text-ink">{windowText(w.starts_at, w.ends_at)}</p>
                <p className="break-words text-xs text-muted">
                  {w.scope === "all" ? t("admin.itsmChange.freeze.allServices") : w.service_ids.map(serviceName).join(", ")}
                  {w.recurrence && (
                    <>
                      {" · "}
                      {t("admin.itsmChange.freeze.recurs", {
                        type: t(`calendar.recurrence.${w.recurrence.type}`, { defaultValue: w.recurrence.type }),
                        interval: w.recurrence.interval,
                      })}
                    </>
                  )}
                  {w.allow_emergency && w.enforcement === "block" && <> · {t("admin.itsmChange.freeze.allowEmergency")}</>}
                </p>
                {w.note && <p className="whitespace-pre-line break-words text-xs text-muted">{w.note}</p>}
              </div>
              <div className="flex gap-2">
                <Button size="sm" onClick={() => setEditor({ window: w })} data-testid="freeze-edit">
                  {t("common.edit")}
                </Button>
                <Button size="sm" variant="danger" onClick={() => setDeleting(w)} data-testid="freeze-delete">
                  {t("common.delete")}
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {editor && <FreezeWindowDialog key={editor.window?.id ?? "new"} window={editor.window} onClose={() => setEditor(null)} />}
      <ConfirmDialog
        open={deleting !== null}
        title={t("admin.itsmChange.freeze.deleteTitle")}
        message={
          <>
            <p>{t("admin.itsmChange.freeze.deleteMessage", { name: deleting?.name ?? "" })}</p>
            {remove.isError && (
              <p className="mt-2 text-danger" role="alert" data-testid="freeze-delete-error">
                {changeAdminErrorText(remove.error, t)}
              </p>
            )}
          </>
        }
        variant="danger"
        confirmLabel={t("common.delete")}
        pending={remove.isPending}
        onConfirm={() => deleting && remove.mutate(deleting)}
        onCancel={() => {
          remove.reset();
          setDeleting(null);
        }}
      />
    </section>
  );
}
