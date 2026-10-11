import { useId, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Spinner } from "@/components/ui/Spinner";
import { apiErrorMessage } from "@/lib/avatarApi";
import { dashboardKeys, dashboardsApi, type Dashboard, type DashboardTab } from "@/lib/dashboardsApi";

const INPUT =
  "w-full rounded-md border border-hairline bg-surface-subtle px-3 py-2 text-sm text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";

type Mode = "me" | "groups";

/**
 * "Teilen": a dashboard is either private ("Nur ich", saves an empty list) or shared with groups the
 * owner may share with (`GET /reference/share-groups`). Members see only tickets from queues they
 * may read themselves, while the widgets' titles, notes and filter settings are visible to every member
 * (R-DASH-SHARE-NOTE); the dialog says both. A server refusal (422, `group_ids`) is shown under the list.
 */
export function ShareDialog({ dashboard, onClose }: { dashboard: DashboardTab; onClose: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const ids = useId();
  const [mode, setMode] = useState<Mode>(dashboard.kind === "shared" || dashboard.group_ids.length > 0 ? "groups" : "me");
  const [picked, setPicked] = useState<Set<number>>(() => new Set(dashboard.group_ids));
  const [search, setSearch] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const groupsQ = useQuery({
    queryKey: ["dashboards", "share-groups"],
    queryFn: ({ signal }) => dashboardsApi.shareGroups(signal),
    staleTime: 60_000,
  });
  const needle = search.trim().toLocaleLowerCase();
  const groups = (groupsQ.data ?? []).filter((g) => !needle || g.name.toLocaleLowerCase().includes(needle));
  // Groups the owner may no longer share with have no checkbox: they drop out of the selection.
  const chosen = [...picked].filter((id) => !groupsQ.data || groupsQ.data.some((g) => g.id === id));
  const canSave = !pending && (mode === "me" || chosen.length > 0);
  const needsGroup = mode === "groups" && chosen.length === 0;

  const toggle = (id: number) => {
    setError(null);
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const save = async () => {
    if (!canSave) return;
    setPending(true);
    setError(null);
    try {
      const wanted = mode === "me" ? [] : chosen.sort((a, b) => a - b);
      const saved: Dashboard = await dashboardsApi.share(dashboard.id, wanted);
      qc.setQueryData(dashboardKeys.one(saved.id), saved);
      const { widgets: _widgets, ...tab } = saved;
      qc.setQueryData<DashboardTab[]>(dashboardKeys.list(), (prev) => prev?.map((x) => (x.id === saved.id ? tab : x)));
      void qc.invalidateQueries({ queryKey: dashboardKeys.list(), exact: true });
      onClose();
    } catch (err) {
      setError(apiErrorMessage(err, t("dashboards.share.failed")));
      setPending(false);
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title={t("dashboards.share.title")}
      description={t("dashboards.share.intro", { name: dashboard.name })}
      size="sm"
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={pending}>
            {t("dashboards.share.cancel")}
          </Button>
          <Button
            variant="primary"
            onClick={() => void save()}
            disabled={!canSave}
            aria-describedby={needsGroup ? `${ids}-pick` : undefined}
          >
            {pending ? <Spinner /> : t("dashboards.share.save")}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div role="radiogroup" aria-label={t("dashboards.share.title")} className="space-y-2">
          {(["me", "groups"] as const).map((m) => (
            <label key={m} className="flex items-center gap-2 text-sm text-ink">
              <input type="radio" name={`${ids}-mode`} checked={mode === m} onChange={() => {
                  setError(null);
                  setMode(m);
                }} />
              {m === "me" ? t("dashboards.share.onlyMe") : t("dashboards.share.groups")}
            </label>
          ))}
        </div>
        {mode === "groups" && (
          <div className="space-y-2">
            <input
              type="search"
              aria-label={t("dashboards.share.search")}
              placeholder={t("dashboards.share.search")}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className={INPUT}
            />
            <div role="group" aria-label={t("dashboards.share.groupsLabel")} className="max-h-56 space-y-1.5 overflow-y-auto">
              {groupsQ.isPending && <Spinner />}
              {groupsQ.isError && <p className="text-sm text-danger">{t("dashboards.share.loadFailed")}</p>}
              {groupsQ.isSuccess && groupsQ.data.length === 0 && (
                <p className="text-sm text-muted">{t("dashboards.share.noGroups")}</p>
              )}
              {groupsQ.isSuccess && groupsQ.data.length > 0 && groups.length === 0 && (
                <p className="text-sm text-muted">{t("dashboards.share.noMatch")}</p>
              )}
              {groups.map((g) => (
                <label key={g.id} className="flex items-center gap-2 text-sm text-ink">
                  <input type="checkbox" checked={picked.has(g.id)} onChange={() => toggle(g.id)} />
                  {g.name}
                </label>
              ))}
            </div>
            {needsGroup && (
              <p id={`${ids}-pick`} className="text-xs text-muted">
                {t("dashboards.share.pickOne")}
              </p>
            )}
            {error && (
              <p role="alert" className="text-sm text-danger">
                {error}
              </p>
            )}
            <p className="text-xs text-muted">{t("dashboards.share.note")}</p>
          </div>
        )}
        {mode === "me" && error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
      </div>
    </Dialog>
  );
}
