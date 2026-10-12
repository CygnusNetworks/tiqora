import { useState } from "react";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { TicketTable } from "@/components/agent/TicketTable";
import { toTableSortKey, toTicketSortKey } from "@/components/searches/searchSort";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { ApiError } from "@/lib/api";
import { DashboardConflictError, dashboardKeys, dashboardsApi, type Dashboard } from "@/lib/dashboardsApi";
import { reportErrorMessage } from "@/lib/reportsApi";
import { settingsWithDefaults } from "../definitions";
import { firstFreeSpot } from "../layout/grid";
import { COLS, toEditWidget } from "../model";
import { CORE_WIDGETS } from "../widgets/index";
import type { DrillSearch } from "./drillLinks";

const PAGE_SIZE = 50;
const LIST_KEY = "core.ticket-list";

function newWidgetId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `w${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;
}

function Notice({ testId, children }: { testId: string; children: React.ReactNode }) {
  return (
    <div className="m-6 rounded-lg border border-hairline bg-surface p-8 text-center text-sm text-ink" data-testid={testId}>
      {children}
    </div>
  );
}

/** Appends a ticket-list widget on the current dashboard (fresh read, first free spot, one save). */
async function appendTicketList(dashboardId: number, filter: Record<string, unknown>): Promise<Dashboard> {
  const def = CORE_WIDGETS.find((w) => w.key === LIST_KEY);
  if (!def) throw new Error("ticket list widget missing");
  const current = await dashboardsApi.get(dashboardId);
  const rects = current.widgets.map((w) => ({
    id: w.id,
    rect: w.layout.lg,
    min: [1, 1] as [number, number],
    max: [COLS.lg, 24] as [number, number],
  }));
  const [w, h] = def.size.default;
  const spot = firstFreeSpot(rects, w, h, COLS.lg);
  const fresh = {
    id: newWidgetId(),
    type_key: def.key,
    type_version: def.version,
    title_override: null,
    settings: { ...settingsWithDefaults(def, {}, def.version), source: { kind: "filter", filter } },
    layout: { lg: { ...spot, w, h } },
    pinned: false,
  };
  return dashboardsApi.save(dashboardId, {
    name: current.name,
    icon: current.icon,
    version: current.version,
    filters: current.filters,
    widgets: [...current.widgets.map((x) => ({ ...toEditWidget(x), pinned: x.pinned ?? false })), fresh],
  });
}

/** `/agent/tickets/drill`: the tickets behind a dashboard widget (or one chart point), paged, read-only. */
export function DrillPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const search = useSearch({ strict: false }) as DrillSearch;
  const { d, w, point } = search;
  const sort = search.sort ?? "activity";
  const order = search.order ?? "desc";
  const offset = search.offset ?? 0;
  const valid = d > 0 && w !== "";
  const pointJson = point ? JSON.stringify(point) : undefined;
  const [adding, setAdding] = useState<"idle" | "pending" | "done">("idle");
  const [addError, setAddError] = useState<string | null>(null);

  const dashQ = useQuery({
    queryKey: dashboardKeys.one(d),
    queryFn: ({ signal }) => dashboardsApi.get(d, signal),
    enabled: valid,
    retry: false,
  });
  const drillQ = useQuery({
    queryKey: [...dashboardKeys.all, "drill", d, w, pointJson, sort, order, offset],
    queryFn: ({ signal }) =>
      dashboardsApi.drillTickets(d, w, { offset, limit: PAGE_SIZE, sort, order, point: pointJson }, signal),
    enabled: valid,
    retry: false,
    placeholderData: keepPreviousData,
  });

  const setSearch = (patch: Partial<DrillSearch>) =>
    void navigate({ to: "/agent/tickets/drill", search: { ...search, ...patch }, replace: true });

  const back = (
    <Link
      to="/agent"
      search={valid ? { d } : {}}
      className="text-sm text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
      data-testid="drill-back"
    >
      {t("dashboards.drill.back")}
    </Link>
  );

  if (!valid) {
    return (
      <Notice testId="drill-invalid">
        <p>{t("dashboards.drill.invalid")}</p>
        <div className="mt-3">{back}</div>
      </Notice>
    );
  }
  if (drillQ.isError) {
    const status = drillQ.error instanceof ApiError ? drillQ.error.status : 0;
    return (
      <Notice testId="drill-error">
        <p role="alert">
          {reportErrorMessage(
            drillQ.error,
            status === 404 ? t("dashboards.drill.gone") : t("dashboards.drill.loadFailed"),
          )}
        </p>
        <div className="mt-3">{back}</div>
      </Notice>
    );
  }
  const page = drillQ.data;
  if (!page) {
    return (
      <div className="p-8 text-center" role="status" aria-label={t("common.loading")}>
        <Spinner className="mx-auto" />
      </div>
    );
  }

  const filter = page.filter ?? null;
  const canEdit = dashQ.data?.can_edit === true;
  const saveSearch = () =>
    void navigate({
      to: "/agent/searches/new",
      state: { criteria: filter, name: page.title } as never,
    });
  const addWidget = async () => {
    if (!filter || adding === "pending") return;
    setAdding("pending");
    setAddError(null);
    try {
      await appendTicketList(d, filter);
      void qc.invalidateQueries({ queryKey: dashboardKeys.one(d) });
      void qc.invalidateQueries({ queryKey: dashboardKeys.data(d) });
      setAdding("done");
    } catch (err) {
      setAdding("idle");
      setAddError(
        err instanceof DashboardConflictError ? t("dashboards.drill.conflict") : reportErrorMessage(err, t("dashboards.drill.addFailed")),
      );
    }
  };

  return (
    <div className="min-w-0 flex-1 space-y-3 p-3" data-testid="drill-page">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <div className="min-w-0 flex-1 basis-64">
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="font-display text-xl font-bold tracking-tight text-ink" data-testid="drill-title">
              {t("dashboards.drill.title", { title: page.title })}
            </h1>
            <span
              className="rounded-full bg-accent-dim px-2.5 py-0.5 font-mono text-[11px] tabular-nums text-accent"
              data-testid="drill-total"
            >
              {page.total}
            </span>
          </div>
          <div className="mt-0.5">{back}</div>
        </div>
        {filter && (
          <div className="flex flex-wrap items-center gap-2">
            <Button onClick={saveSearch} data-testid="drill-save-search">
              {t("dashboards.drill.saveSearch")}
            </Button>
            {canEdit && (
              <Button onClick={() => void addWidget()} disabled={adding !== "idle"} data-testid="drill-add-widget">
                {t("dashboards.drill.addWidget")}
              </Button>
            )}
          </div>
        )}
      </div>
      {page.note && (
        <p className="text-sm text-muted" data-testid="drill-note">
          {page.note}
        </p>
      )}
      {addError && (
        <p className="text-sm text-danger" role="alert" data-testid="drill-add-error">
          {addError}
        </p>
      )}
      {adding === "done" && (
        <p className="text-sm text-ink" role="status" data-testid="drill-added">
          {t("dashboards.drill.added")}
        </p>
      )}
      <TicketTable
        items={page.items}
        total={page.total}
        offset={offset}
        limit={PAGE_SIZE}
        sort={toTableSortKey(toTicketSortKey(sort))}
        order={order}
        isLoading={drillQ.isFetching}
        onSortChange={(s, o) => setSearch({ sort: toTicketSortKey(s), order: o, offset: undefined })}
        onPageChange={(off) => setSearch({ offset: off || undefined })}
        navContext={{
          label: page.title,
          to: "/agent/tickets/drill",
          search: { d, w, point, offset: offset || undefined, sort: search.sort, order: search.order },
        }}
      />
    </div>
  );
}
