import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { toBcp47 } from "@/i18n";
import { api } from "@/lib/api";
import { SelectField } from "@/components/ui/SelectField";
import type { SelectMenuItem } from "@/components/ui/SelectMenu";
import { Spinner } from "@/components/ui/Spinner";
import { formatDateTime } from "@/lib/format";

export type TimeAccountingSearch = {
  create_by?: number;
  ticket_id?: number;
  created_from?: string;
  created_to?: string;
  offset?: number;
};

/**
 * Agent time-accounting report: list booked time units with filters for
 * user, date range, and ticket — backed by GET /api/v1/tickets/time-accounting.
 */
export function TimeAccountingReportPage() {
  const { t, i18n } = useTranslation();
  const locale = toBcp47(i18n.language);
  const navigate = useNavigate({ from: "/agent/time-accounting" });
  const search = useSearch({ from: "/agent/time-accounting" }) as TimeAccountingSearch;
  const offset = search.offset ?? 0;
  const limit = 100;

  const setSearch = (patch: Partial<TimeAccountingSearch>) => {
    void navigate({
      search: (prev) => {
        const next = { ...(prev as TimeAccountingSearch), ...patch };
        if (patch.offset === undefined && Object.keys(patch).some((k) => k !== "offset")) {
          next.offset = 0;
        }
        if (!next.create_by) delete next.create_by;
        if (!next.ticket_id) delete next.ticket_id;
        if (!next.created_from) delete next.created_from;
        if (!next.created_to) delete next.created_to;
        if (!next.offset) delete next.offset;
        return next;
      },
      replace: true,
    });
  };

  const agentsQ = useQuery({
    queryKey: ["reference", "agents"],
    queryFn: () => api.listReferenceAgents(),
  });

  const reportQ = useQuery({
    queryKey: [
      "time-accounting-report",
      {
        create_by: search.create_by,
        ticket_id: search.ticket_id,
        created_from: search.created_from,
        created_to: search.created_to,
        offset,
        limit,
      },
    ],
    queryFn: () =>
      api.listTimeAccountingReport({
        create_by: search.create_by,
        ticket_id: search.ticket_id,
        created_from: search.created_from
          ? `${search.created_from}T00:00:00`
          : undefined,
        created_to: search.created_to ? `${search.created_to}T23:59:59` : undefined,
        offset,
        limit,
      }),
  });

  const agentItems: SelectMenuItem<number>[] = useMemo(() => {
    const base = (agentsQ.data ?? []).map((a) => ({
      value: a.id,
      label: a.full_name,
      hint: a.login,
    }));
    // Sentinel 0 clears the user filter via SelectField (no native clear).
    return [{ value: 0, label: t("timeAccounting.allUsers") }, ...base];
  }, [agentsQ.data, t]);

  const items = reportQ.data?.items ?? [];
  const totalUnits = reportQ.data?.total_units ?? 0;

  return (
    <div
      className="mx-auto w-full max-w-6xl space-y-4 px-4 py-6"
      data-testid="time-accounting-report"
    >
      <div>
        <h1 className="font-display text-xl font-bold tracking-tight text-ink">
          {t("timeAccounting.title")}
        </h1>
        <p className="mt-0.5 text-[12.5px] text-muted">{t("timeAccounting.hint")}</p>
      </div>

      <div className="flex flex-wrap items-end gap-3 rounded-lg border border-hairline bg-surface p-3">
        <div className="min-w-[12rem]">
          <label className="mb-1 block text-xs font-medium text-muted">
            {t("timeAccounting.filterUser")}
          </label>
          <SelectField
            items={agentItems}
            value={search.create_by ?? 0}
            onChange={(id) => setSearch({ create_by: id === 0 ? undefined : id })}
            placeholder={t("timeAccounting.allUsers")}
            testId="ta-filter-user"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-muted">
            {t("timeAccounting.filterFrom")}
          </label>
          <input
            type="date"
            data-testid="ta-filter-from"
            value={search.created_from ?? ""}
            onChange={(e) => setSearch({ created_from: e.target.value || undefined })}
            className="rounded-md border border-hairline bg-surface-subtle px-2 py-1.5 text-sm text-ink"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-muted">
            {t("timeAccounting.filterTo")}
          </label>
          <input
            type="date"
            data-testid="ta-filter-to"
            value={search.created_to ?? ""}
            onChange={(e) => setSearch({ created_to: e.target.value || undefined })}
            className="rounded-md border border-hairline bg-surface-subtle px-2 py-1.5 text-sm text-ink"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-muted">
            {t("timeAccounting.filterTicket")}
          </label>
          <input
            type="number"
            data-testid="ta-filter-ticket"
            placeholder="ID"
            value={search.ticket_id ?? ""}
            onChange={(e) => {
              const v = e.target.value;
              setSearch({ ticket_id: v ? Number(v) : undefined });
            }}
            className="w-28 rounded-md border border-hairline bg-surface-subtle px-2 py-1.5 font-mono text-sm text-ink"
          />
        </div>
        <button
          type="button"
          data-testid="ta-filter-clear"
          className="text-xs font-medium text-accent hover:underline"
          onClick={() =>
            setSearch({
              create_by: undefined,
              ticket_id: undefined,
              created_from: undefined,
              created_to: undefined,
              offset: 0,
            })
          }
        >
          {t("search.filters.clear")}
        </button>
      </div>

      <div className="flex items-center justify-between text-sm">
        <span className="font-medium text-ink" data-testid="ta-total-units">
          {t("timeAccounting.totalUnits", { units: totalUnits.toFixed(2) })}
        </span>
        <span className="text-muted">
          {t("timeAccounting.rowCount", { count: items.length })}
        </span>
      </div>

      {reportQ.isLoading ? (
        <div className="flex justify-center py-10">
          <Spinner />
        </div>
      ) : items.length === 0 ? (
        <p className="rounded-lg border border-hairline bg-surface px-4 py-8 text-center text-sm text-muted">
          {t("timeAccounting.empty")}
        </p>
      ) : (
        <div className="overflow-hidden rounded-lg border border-hairline bg-surface">
          <table className="w-full text-left text-sm" data-testid="ta-table">
            <thead className="bg-surface-subtle text-xs uppercase tracking-wide text-muted">
              <tr>
                <th className="px-3 py-2 font-semibold">{t("timeAccounting.colWhen")}</th>
                <th className="px-3 py-2 font-semibold">{t("timeAccounting.colTicket")}</th>
                <th className="px-3 py-2 font-semibold">{t("timeAccounting.colUser")}</th>
                <th className="px-3 py-2 text-right font-semibold">
                  {t("timeAccounting.colUnits")}
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-hairline">
              {items.map((row) => (
                <tr key={row.id} className="hover:bg-surface-subtle/60">
                  <td className="whitespace-nowrap px-3 py-2 text-muted">
                    {row.create_time ? formatDateTime(row.create_time, locale) : "—"}
                  </td>
                  <td className="px-3 py-2">
                    <Link
                      to="/agent/tickets/$ticketId"
                      params={{ ticketId: String(row.ticket_id) }}
                      className="font-medium text-accent hover:underline"
                    >
                      {row.ticket_tn || `#${row.ticket_id}`}
                    </Link>
                    {row.ticket_title && (
                      <span className="ml-1.5 text-muted">{row.ticket_title}</span>
                    )}
                  </td>
                  <td className="px-3 py-2 font-mono text-xs">
                    {row.create_by_login || row.create_by}
                  </td>
                  <td className="px-3 py-2 text-right font-mono tabular-nums">
                    {row.time_unit.toFixed(2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="flex justify-between">
        <button
          type="button"
          disabled={offset <= 0}
          className="text-sm font-medium text-accent disabled:opacity-40"
          onClick={() => setSearch({ offset: Math.max(0, offset - limit) })}
        >
          {t("common.prev")}
        </button>
        <button
          type="button"
          disabled={items.length < limit}
          className="text-sm font-medium text-accent disabled:opacity-40"
          onClick={() => setSearch({ offset: offset + limit })}
        >
          {t("common.next")}
        </button>
      </div>
    </div>
  );
}
