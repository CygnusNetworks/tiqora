import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";
import { toBcp47 } from "@/i18n";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";
import { dashboardKeys, dashboardsApi, type Dashboard, type DashboardTab } from "@/lib/dashboardsApi";
import { displayTimeZone } from "@/lib/timeZone";
import { DashboardGrid } from "@/dashboard/DashboardGrid";
import { useDashboardData } from "@/dashboard/data";
import { DashboardEditMode } from "@/dashboard/edit/DashboardEditMode";
import { greetingKey } from "@/dashboard/legacy";
import { toEditWidget } from "@/dashboard/model";
import { ShareDialog } from "@/dashboard/tabs/ShareDialog";
import { ManagedDashboardTabs } from "@/dashboard/tabs/ManagedDashboardTabs";
import { useGridBreakpoint } from "@/dashboard/useGridBreakpoint";
import { useSelectedDashboard } from "@/dashboard/useSelectedDashboard";
import { registerCoreWidgets } from "@/dashboard/widgets";

/** Search params of the home page: `?d=<dashboard id>` opens that dashboard. */
export interface DashboardSearch {
  d?: number;
}

// Once per page load, before the first read of the widget registry (idempotent).
registerCoreWidgets();

function tabOf(d: Dashboard): DashboardTab {
  const { widgets: _widgets, ...tab } = d;
  return tab;
}

const notThere = (err: unknown) => err instanceof ApiError && (err.status === 404 || err.status === 403);

const BUTTON =
  "rounded-lg px-3.5 py-2 text-[13px] transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-50";

function ErrorBox({ message, onRetry }: { message: string; onRetry: () => void }) {
  const { t } = useTranslation();
  return (
    <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-[14px] border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">
      <span>{message}</span>
      <button
        type="button"
        onClick={onRetry}
        className={cn(BUTTON, "border border-hairline bg-surface py-1.5 text-xs font-medium text-ink hover:bg-surface-subtle")}
      >
        {t("dashboards.state.retry")}
      </button>
    </div>
  );
}

/**
 * The agent home page: greeting, the tab row of the agent's dashboards and the widget grid of the
 * selected one. First visit (no own dashboard yet, R-DASH-FIRST-VISIT) creates "Mein Start".
 * "Anpassen" switches to edit mode: the edit toolbar replaces greeting, tabs and header buttons
 * until "Speichern" or "Verwerfen". "Teilen" opens `ShareDialog`; the tabs manage
 * dashboards themselves (`ManagedDashboardTabs`).
 */
export function DashboardPage() {
  const { t, i18n } = useTranslation();
  const { user } = useAuth();
  const qc = useQueryClient();
  const { d } = useSearch({ strict: false });
  const locale = toBcp47(i18n.language);

  const listQ = useQuery({
    queryKey: dashboardKeys.list(),
    queryFn: ({ signal }) => dashboardsApi.list(signal),
  });
  const tabs = listQ.data;
  const { selectedId, select, reject } = useSelectedDashboard(tabs, { d });
  const navigate = useNavigate();
  const panelId = useId();
  // A picked tab also drops `?d=`, so a reload opens the picked dashboard, not the linked one.
  const onSelect = useCallback(
    (id: number) => {
      select(id);
      if (d != null) void navigate({ to: ".", search: {}, replace: true });
    },
    [select, d, navigate],
  );

  // First visit: nobody owns a dashboard yet (the list may hold locked role templates).
  const ensured = useRef(false);
  const [ensureFailed, setEnsureFailed] = useState(false);
  const [ensureAttempt, setEnsureAttempt] = useState(0);
  useEffect(() => {
    if (!tabs || ensured.current || tabs.some((tab) => tab.is_owner)) return;
    ensured.current = true;
    dashboardsApi
      .ensureStart()
      .then((start) => {
        qc.setQueryData(dashboardKeys.one(start.id), start);
        qc.setQueryData<DashboardTab[]>(dashboardKeys.list(), (prev = []) =>
          prev.some((tab) => tab.id === start.id) ? prev : [tabOf(start), ...prev],
        );
        const dIsTab = d != null && d !== start.id && tabs.some((tab) => tab.id === d);
        if (!dIsTab) select(start.id);
        void qc.invalidateQueries({ queryKey: dashboardKeys.list(), exact: true });
      })
      .catch(() => setEnsureFailed(true));
  }, [tabs, qc, d, select, ensureAttempt]);

  const dashQ = useQuery({
    queryKey: dashboardKeys.one(selectedId ?? 0),
    queryFn: ({ signal }) => dashboardsApi.get(selectedId!, signal),
    enabled: selectedId != null,
    retry: false,
  });
  const isTab = selectedId != null && (tabs ?? []).some((tab) => tab.id === selectedId);
  // An admin's `?d=` that is not a tab and does not exist: fall back to the tabs.
  useEffect(() => {
    if (selectedId != null && !isTab && dashQ.isError && notThere(dashQ.error)) reject(selectedId);
  }, [selectedId, isTab, dashQ.isError, dashQ.error, reject]);

  const dashboard = dashQ.data && dashQ.data.id === selectedId ? dashQ.data : undefined;
  const widgets = useMemo(() => (dashboard ? dashboard.widgets.map(toEditWidget) : []), [dashboard]);
  const [editing, setEditing] = useState(false);
  const [sharing, setSharing] = useState(false);
  const isEditing = editing && dashboard !== undefined && dashboard.can_edit && user != null;
  // While editing nothing is fetched for the view; its last data is reused by the edit grid.
  const { byWidget, refetch } = useDashboardData(dashboard?.id ?? 0, widgets, {
    enabled: dashboard !== undefined && !isEditing,
  });
  // Another dashboard (e.g. a link with ?d=) never opens in edit mode by itself.
  useEffect(() => {
    setEditing(false);
  }, [selectedId]);
  // Back in view mode the focus returns to "Anpassen".
  const customizeRef = useRef<HTMLButtonElement>(null);
  const returnFocus = useRef(false);
  useEffect(() => {
    if (isEditing || !returnFocus.current) return;
    returnFocus.current = false;
    customizeRef.current?.focus();
  }, [isEditing]);
  const leaveEdit = useCallback(() => {
    returnFocus.current = true;
    setEditing(false);
  }, []);
  const reloadAfterConflict = useCallback(() => {
    if (dashboard) {
      void qc.invalidateQueries({ queryKey: dashboardKeys.one(dashboard.id) });
      void qc.invalidateQueries({ queryKey: dashboardKeys.data(dashboard.id) });
      void qc.invalidateQueries({ queryKey: dashboardKeys.list() });
    }
    leaveEdit();
  }, [dashboard, qc, leaveEdit]);

  const containerRef = useRef<HTMLDivElement>(null);
  // Column beside the grid for the widget gallery in edit mode (the grid's width, and so its
  // breakpoint, follows whether the gallery is open).
  const [galleryHost, setGalleryHost] = useState<HTMLElement | null>(null);
  const breakpoint = useGridBreakpoint(containerRef);

  const greeting = t(`dashboard.greeting.${greetingKey()}`, { name: user?.first_name || user?.login || "" });
  const today = new Intl.DateTimeFormat(locale, {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone: displayTimeZone(),
  }).format(new Date());

  let content;
  if (isEditing && dashboard && user) {
    content = (
      <DashboardEditMode
        dashboard={dashboard}
        userId={user.id}
        breakpoint={breakpoint}
        savedWidgets={widgets}
        savedData={byWidget}
        onLeave={leaveEdit}
        onReload={reloadAfterConflict}
        galleryHost={galleryHost}
      />
    );
  } else if (listQ.isError) {
    content = <ErrorBox message={t("dashboards.view.listFailed")} onRetry={() => void listQ.refetch()} />;
  } else if (ensureFailed) {
    content = (
      <ErrorBox
        message={t("dashboards.view.loadFailed")}
        onRetry={() => {
          ensured.current = false;
          setEnsureFailed(false);
          setEnsureAttempt((n) => n + 1);
        }}
      />
    );
  } else if (dashQ.isError && !(notThere(dashQ.error) && !isTab)) {
    content = <ErrorBox message={t("dashboards.view.loadFailed")} onRetry={() => void dashQ.refetch()} />;
  } else if (!dashboard) {
    content = (
      <div
        role="status"
        aria-busy="true"
        aria-label={t("dashboards.view.loading")}
        className="flex flex-col gap-3 rounded-[14px] border border-hairline bg-surface px-[18px] py-4"
      >
        {["92%", "74%", "84%", "62%", "92%", "74%"].map((width, i) => (
          <div key={i} className="h-3.5 rounded bg-surface-subtle motion-safe:animate-pulse" style={{ width }} />
        ))}
      </div>
    );
  } else if (widgets.length === 0) {
    content = (
      <p className="rounded-[14px] border border-dashed border-hairline bg-surface px-4 py-8 text-center text-sm text-muted">
        {dashboard.can_edit ? t("dashboards.view.emptyEdit") : t("dashboards.view.empty")}
      </p>
    );
  } else {
    content = (
      <DashboardGrid
        widgets={widgets}
        mode="view"
        breakpoint={breakpoint}
        dashboardId={dashboard.id}
        canEdit={dashboard.can_edit}
        data={byWidget}
        onRetry={refetch}
      />
    );
  }

  return (
    <div className="mx-auto w-full max-w-7xl space-y-5 px-4 py-6 sm:px-8" data-testid="dashboard">
      {!isEditing && (
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex min-w-0 flex-col gap-2.5">
          <div>
            <h1 className="font-display text-2xl font-semibold tracking-tight text-ink">{greeting}</h1>
            <p className="mt-1 text-sm text-muted">{today}</p>
          </div>
          {tabs && tabs.length > 0 && <ManagedDashboardTabs tabs={tabs} selectedId={selectedId} onSelect={onSelect} panelId={panelId} />}
        </div>
        {dashboard && (dashboard.can_share || dashboard.can_edit) && (
          <div className="flex gap-2">
            {dashboard.can_share && (
              <button
                type="button"
                onClick={() => setSharing(true)}
                className={cn(BUTTON, "border border-hairline bg-surface text-ink hover:bg-surface-subtle")}
              >
                {t("dashboards.view.share")}
              </button>
            )}
            {dashboard.can_edit && (
              <button
                ref={customizeRef}
                type="button"
                onClick={() => setEditing(true)}
                className={cn(BUTTON, "bg-accent px-4 font-semibold text-accent-ink hover:bg-accent/90")}
              >
                {t("dashboards.view.customize")}
              </button>
            )}
          </div>
        )}
      </header>
      )}

      {!isEditing && dashboard?.kind === "template" && dashboard.can_edit && (
        <p className="rounded-[14px] border border-accent/30 bg-accent/10 px-4 py-2.5 text-sm text-ink">
          {t("dashboards.view.templateBanner", { name: dashboard.name })}
        </p>
      )}
      {!isEditing && dashboard?.kind === "template" && dashboard.locked && !dashboard.can_edit && (
        <p className="text-sm text-muted">{t("dashboards.view.lockedHint")}</p>
      )}

      <div className={isEditing ? "flex items-start gap-5" : undefined}>
        <div
          ref={containerRef}
          id={panelId}
          role={isEditing ? undefined : "tabpanel"}
          aria-labelledby={!isEditing && isTab ? `${panelId}-tab-${selectedId}` : undefined}
          aria-label={isEditing || isTab ? undefined : dashboard?.name}
          tabIndex={!isEditing && widgets.length === 0 ? 0 : undefined}
          className="min-w-0 flex-1 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          {content}
        </div>
        {isEditing && <div ref={setGalleryHost} className="sticky top-24 w-[340px] flex-none self-start empty:hidden max-lg:hidden" />}
      </div>
      {sharing && dashboard && <ShareDialog dashboard={dashboard} onClose={() => setSharing(false)} />}
    </div>
  );
}
