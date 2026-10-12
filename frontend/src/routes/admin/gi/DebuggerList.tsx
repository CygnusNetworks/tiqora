import { useMemo, useRef, useState } from "react";
import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { Spinner } from "@/components/ui/Spinner";
import { webservicesApi, type DebugEntry, type DebuggerQuery } from "@/lib/webservicesApi";
import { DebuggerEntryDrawer } from "./DebuggerEntryDrawer";
import { levelTone } from "./debugLevel";

type Direction = "all" | "Provider" | "Requester";
type Source = "all" | "tiqora" | "znuny";

function dayStart(day: string, plusDays = 0): string | undefined {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return undefined;
  const d = new Date(`${day}T00:00:00`);
  if (Number.isNaN(d.getTime())) return undefined;
  d.setDate(d.getDate() + plusDays);
  return d.toISOString();
}

const entryKey = (e: DebugEntry) => `${e.source}-${e.id}`;

/**
 * "Protokoll (Debugger)": Tiqora's and Znuny's debugger entries of one
 * webservice, newest first. Filters go to the server; "Weitere laden" walks
 * the opaque `next_before` cursor.
 */
export function DebuggerList({ wsId, debugThreshold }: { wsId: number; debugThreshold?: string }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [direction, setDirection] = useState<Direction>("all");
  const [source, setSource] = useState<Source>("all");
  const [fromDay, setFromDay] = useState("");
  const [toDay, setToDay] = useState("");
  const [ipDraft, setIpDraft] = useState("");
  const [ip, setIp] = useState("");
  const [selected, setSelected] = useState<{ entry: DebugEntry; resend: boolean } | null>(null);
  const resendQueued = useRef(false);
  const refreshList = () =>
    void qc.invalidateQueries({ queryKey: ["admin", "webservices", wsId, "debugger", "list"] });

  const filters = useMemo<DebuggerQuery>(
    () => ({
      type: direction === "all" ? undefined : direction,
      source: source === "all" ? undefined : source,
      from: dayStart(fromDay),
      // the "to" day is inclusive: stop at the start of the next day
      to: dayStart(toDay, 1),
      remote_ip: ip || undefined,
    }),
    [direction, source, fromDay, toDay, ip],
  );
  const queryKey = ["admin", "webservices", wsId, "debugger", "list", filters] as const;

  const q = useInfiniteQuery({
    queryKey,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) =>
      webservicesApi.debuggerList(wsId, { ...filters, before: pageParam }, signal),
    getNextPageParam: (last) => last.next_before ?? undefined,
  });

  const entries = useMemo(() => {
    const seen = new Set<string>();
    const out: DebugEntry[] = [];
    for (const p of q.data?.pages ?? []) {
      for (const e of p.entries) {
        const k = entryKey(e);
        if (!seen.has(k)) {
          seen.add(k);
          out.push(e);
        }
      }
    }
    return out;
  }, [q.data]);

  const commitIp = () => setIp(ipDraft.trim());

  const dirItems = [
    { value: "all" as const, label: t("admin.webservices.debugger.directionAll") },
    { value: "Provider" as const, label: t("admin.webservices.debugger.directionIn") },
    { value: "Requester" as const, label: t("admin.webservices.debugger.directionOut") },
  ];
  const sourceItems = [
    { value: "all" as const, label: t("admin.webservices.debugger.sourceAll") },
    { value: "tiqora" as const, label: t("admin.webservices.debugger.sourceTiqora") },
    { value: "znuny" as const, label: t("admin.webservices.debugger.sourceZnuny") },
  ];
  const inputCls =
    "rounded-lg border border-hairline bg-surface px-2 py-1.5 text-sm text-ink";

  return (
    <div className="space-y-3" data-testid="ws-tab-debugger">
      <p className="text-sm text-muted">{t("admin.webservices.debugger.description")}</p>

      <div
        className="flex flex-wrap items-end gap-x-4 gap-y-2 rounded-xl border border-hairline bg-surface p-3"
        role="group"
        aria-label={t("admin.webservices.debugger.filters")}
      >
        <div>
          <span id="dbg-dir" className="mb-1 block text-xs font-medium text-muted">
            {t("admin.webservices.debugger.direction")}
          </span>
          <SegmentedControl
            items={dirItems}
            value={direction}
            onChange={setDirection}
            aria-labelledby="dbg-dir"
            testId="debug-filter-direction"
          />
        </div>
        <div>
          <span id="dbg-src" className="mb-1 block text-xs font-medium text-muted">
            {t("admin.webservices.debugger.source")}
          </span>
          <SegmentedControl
            items={sourceItems}
            value={source}
            onChange={setSource}
            aria-labelledby="dbg-src"
            testId="debug-filter-source"
          />
        </div>
        <label className="text-xs font-medium text-muted">
          <span className="mb-1 block">{t("admin.webservices.debugger.from")}</span>
          <input
            type="date"
            value={fromDay}
            onChange={(e) => setFromDay(e.target.value)}
            className={inputCls}
            data-testid="debug-filter-from"
          />
        </label>
        <label className="text-xs font-medium text-muted">
          <span className="mb-1 block">{t("admin.webservices.debugger.to")}</span>
          <input
            type="date"
            value={toDay}
            onChange={(e) => setToDay(e.target.value)}
            className={inputCls}
            data-testid="debug-filter-to"
          />
        </label>
        <label className="text-xs font-medium text-muted">
          <span className="mb-1 block">{t("admin.webservices.debugger.ip")}</span>
          <input
            value={ipDraft}
            maxLength={50}
            placeholder={t("admin.webservices.debugger.ipPlaceholder")}
            onChange={(e) => setIpDraft(e.target.value)}
            onBlur={commitIp}
            onKeyDown={(e) => {
              if (e.key === "Enter") commitIp();
            }}
            className={`${inputCls} font-mono`}
            data-testid="debug-filter-ip"
          />
        </label>
      </div>

      {q.isLoading && <Spinner />}
      {q.isError && (
        <p className="text-sm text-danger" role="alert" data-testid="debug-error">
          {t("admin.webservices.debugger.loadError")}
        </p>
      )}
      {q.data && entries.length === 0 && (
        <p className="text-sm text-muted" data-testid="debug-empty">
          {t("admin.webservices.debugger.empty")}
        </p>
      )}
      {entries.length > 0 && (
        <ul className="divide-y divide-hairline rounded-xl border border-hairline bg-surface" data-testid="debug-rows">
          {entries.map((e) => {
            const key = entryKey(e);
            return (
              <li key={key} className="flex flex-wrap items-center gap-2 px-3 py-2">
                <button
                  type="button"
                  className="flex min-w-0 flex-1 flex-wrap items-center gap-x-3 gap-y-1 rounded text-left hover:bg-surface-subtle"
                  onClick={() => setSelected({ entry: e, resend: false })}
                  data-testid={`debug-row-${key}`}
                >
                  <span className="sr-only">{t("admin.webservices.debugger.open", { id: e.id })}:</span>
                  <span className="font-mono text-xs text-muted">{new Date(e.created).toLocaleString()}</span>
                  <Badge tone="muted">
                    {e.communication_type === "Provider"
                      ? t("admin.webservices.debugger.directionIn")
                      : t("admin.webservices.debugger.directionOut")}
                  </Badge>
                  <Badge tone={e.source === "znuny" ? "default" : "accent"}>
                    {e.source === "znuny"
                      ? t("admin.webservices.debugger.sourceZnuny")
                      : t("admin.webservices.debugger.sourceTiqora")}
                  </Badge>
                  <span className="truncate text-sm font-medium text-ink">{e.name ?? "—"}</span>
                  {e.remote_ip && <span className="font-mono text-xs text-muted">{e.remote_ip}</span>}
                  {e.ticket_id != null && (
                    <span className="text-xs text-muted">
                      {t("admin.webservices.debugger.ticket", { id: e.ticket_id })}
                    </span>
                  )}
                  <Badge tone={levelTone(e.max_level)} data-testid={`debug-level-${key}`}>
                    {t(`admin.webservices.debugger.levels.${e.max_level}`, { defaultValue: e.max_level })}
                  </Badge>
                </button>
                {e.can_resend && (
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => setSelected({ entry: e, resend: true })}
                    data-testid={`debug-resend-${key}`}
                  >
                    {t("admin.webservices.debugger.resend")}
                  </Button>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {q.hasNextPage && (
        <Button
          variant="secondary"
          size="sm"
          onClick={() => void q.fetchNextPage()}
          disabled={q.isFetchingNextPage}
          data-testid="debug-load-more"
        >
          {q.isFetchingNextPage ? t("admin.webservices.debugger.loadingMore") : t("admin.webservices.debugger.loadMore")}
        </Button>
      )}

      {selected && (
        <DebuggerEntryDrawer
          key={`${entryKey(selected.entry)}-${selected.resend}`}
          wsId={wsId}
          source={selected.entry.source}
          entryId={selected.entry.id}
          startResend={selected.resend}
          debugThreshold={debugThreshold}
          onResendStarted={() => {
            resendQueued.current = true;
          }}
          onClose={() => {
            setSelected(null);
            if (resendQueued.current) {
              resendQueued.current = false;
              refreshList();
            }
          }}
          onResent={() => {
            setSelected(null);
            resendQueued.current = false;
            refreshList();
          }}
        />
      )}
    </div>
  );
}
