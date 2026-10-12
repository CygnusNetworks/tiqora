import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { apiErrorMessage } from "@/lib/avatarApi";
import { dashboardKeys, dashboardsApi, type Dashboard, type DashboardTab } from "@/lib/dashboardsApi";
import { DashboardTabs } from "./DashboardTabs";
import { NAME_MAX, NewDashboardDialog, RenameDashboardDialog } from "./NewDashboardDialog";

function tabOf(d: Dashboard): DashboardTab {
  const { widgets: _widgets, ...tab } = d;
  return tab;
}

/**
 * The tab row of the home page with its management: "+" (new, optionally a copy), and the "..." menu
 * of the selected tab (rename / move / copy / delete). Every change goes through `dashboardsApi` and
 * the shared `dashboardKeys.list()` cache; the tab it creates or the neighbour of a deleted one is
 * selected. Dialogs return the focus to the "..." button; after a delete it moves to the neighbour.
 */
export function ManagedDashboardTabs({
  tabs,
  selectedId,
  onSelect,
  panelId,
  onWallboard,
}: {
  tabs: DashboardTab[];
  selectedId: number | null;
  onSelect: (id: number) => void;
  panelId?: string;
  /** Opens the TV mode of a dashboard ("Als Wallboard anzeigen"). */
  onWallboard?: (tab: DashboardTab) => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [renaming, setRenaming] = useState<DashboardTab | null>(null);
  const [deleting, setDeleting] = useState<DashboardTab | null>(null);
  const [deletePending, setDeletePending] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const rowRef = useRef<HTMLDivElement | null>(null);
  const [focusTab, setFocusTab] = useState<number | null>(null);

  // Runs after the confirm dialog's cleanup has refocused its opener, so the neighbour tab wins.
  useEffect(() => {
    if (focusTab === null) return;
    rowRef.current?.querySelector<HTMLElement>(`[data-testid="dashboard-tab-${focusTab}"]`)?.focus();
    setFocusTab(null);
  }, [focusTab, tabs]);

  const created = (d: Dashboard) => {
    qc.setQueryData(dashboardKeys.one(d.id), d);
    qc.setQueryData<DashboardTab[]>(dashboardKeys.list(), (prev = []) =>
      prev.some((tab) => tab.id === d.id) ? prev : [...prev, tabOf(d)],
    );
    void qc.invalidateQueries({ queryKey: dashboardKeys.list(), exact: true });
    onSelect(d.id);
  };

  const create = async (name: string, copyFrom: number | null) => {
    created(await dashboardsApi.create({ name, icon: null, copy_from: copyFrom }));
  };

  const copy = async (tab: DashboardTab) => {
    setProblem(null);
    const name = t("dashboards.manage.copyName", { name: tab.name.slice(0, NAME_MAX - 20) });
    try {
      created(await dashboardsApi.create({ name, icon: null, copy_from: tab.id }));
    } catch (err) {
      setProblem(apiErrorMessage(err, t("dashboards.manage.actionFailed")));
    }
  };

  const rename = async (tab: DashboardTab, name: string) => {
    // The current widgets and version, read fresh: a rename is a whole-dashboard save.
    const current = await qc.fetchQuery({
      queryKey: dashboardKeys.one(tab.id),
      queryFn: ({ signal }) => dashboardsApi.get(tab.id, signal),
      staleTime: 0,
    });
    const saved = await dashboardsApi.save(tab.id, {
      name,
      icon: current.icon ?? null,
      version: current.version,
      widgets: current.widgets,
    });
    qc.setQueryData(dashboardKeys.one(tab.id), saved);
    qc.setQueryData<DashboardTab[]>(dashboardKeys.list(), (prev) =>
      prev?.map((x) => (x.id === tab.id ? tabOf(saved) : x)),
    );
    void qc.invalidateQueries({ queryKey: dashboardKeys.list(), exact: true });
    setRenaming(null);
  };

  const move = async (tab: DashboardTab, dir: -1 | 1) => {
    setProblem(null);
    const own = tabs.filter((x) => x.is_owner).map((x) => x.id);
    const from = own.indexOf(tab.id);
    const to = from + dir;
    if (from < 0 || to < 0 || to >= own.length) return;
    [own[from], own[to]] = [own[to], own[from]];
    const previous = qc.getQueryData<DashboardTab[]>(dashboardKeys.list());
    // Optimistic: the own tabs take the new order in the slots they already occupy.
    qc.setQueryData<DashboardTab[]>(dashboardKeys.list(), (prev) => {
      if (!prev) return prev;
      const byId = new Map(prev.map((x) => [x.id, x]));
      let n = 0;
      return prev.map((x) => (x.is_owner ? byId.get(own[n++])! : x));
    });
    try {
      await dashboardsApi.order(own);
      void qc.invalidateQueries({ queryKey: dashboardKeys.list(), exact: true });
    } catch (err) {
      qc.setQueryData(dashboardKeys.list(), previous);
      setProblem(apiErrorMessage(err, t("dashboards.manage.actionFailed")));
    }
  };

  const confirmDelete = async () => {
    const tab = deleting;
    if (!tab) return;
    setDeletePending(true);
    setProblem(null);
    const index = tabs.findIndex((x) => x.id === tab.id);
    const neighbour = tabs[index - 1] ?? tabs[index + 1] ?? null;
    try {
      await dashboardsApi.remove(tab.id);
      qc.removeQueries({ queryKey: dashboardKeys.one(tab.id) });
      qc.setQueryData<DashboardTab[]>(dashboardKeys.list(), (prev) => prev?.filter((x) => x.id !== tab.id));
      void qc.invalidateQueries({ queryKey: dashboardKeys.list(), exact: true });
      if (tab.id === selectedId && neighbour) {
        onSelect(neighbour.id);
        setFocusTab(neighbour.id);
      }
    } catch (err) {
      setProblem(apiErrorMessage(err, t("dashboards.manage.actionFailed")));
    } finally {
      setDeletePending(false);
      setDeleting(null);
    }
  };

  return (
    <>
      <div ref={rowRef} className="contents">
        <DashboardTabs
          tabs={tabs}
          selectedId={selectedId}
          onSelect={onSelect}
          onCreate={() => setCreating(true)}
          panelId={panelId}
          actions={{
            onRename: setRenaming,
            onMove: (tab, dir) => void move(tab, dir),
            onDelete: setDeleting,
            onCopy: (tab) => void copy(tab),
            onWallboard,
          }}
        />
      </div>
      {problem && (
        <p role="alert" className="text-sm text-danger">
          {problem}
        </p>
      )}
      {creating && (
        <NewDashboardDialog
          dashboards={tabs}
          onCreate={async (name, copyFrom) => {
            await create(name, copyFrom ?? null);
            setCreating(false);
          }}
          onClose={() => setCreating(false)}
        />
      )}
      {renaming && (
        <RenameDashboardDialog name={renaming.name} onRename={(name) => rename(renaming, name)} onClose={() => setRenaming(null)} />
      )}
      <ConfirmDialog
        open={deleting !== null}
        role="alertdialog"
        variant="danger"
        title={t("dashboards.manage.deleteTitle", { name: deleting?.name ?? "" })}
        message={t("dashboards.manage.deleteMessage")}
        confirmLabel={t("dashboards.manage.delete")}
        pending={deletePending}
        onConfirm={() => void confirmDelete()}
        onCancel={() => setDeleting(null)}
      />
    </>
  );
}
